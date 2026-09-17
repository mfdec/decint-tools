"""Code Executor: Admin-managed script execution platform.

Admins can upload Python scripts with customizable arguments. Users can select
scripts and versions from a dropdown menu and execute them with custom arguments.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from pydantic import BaseModel, Field

from ..auth import require_admin, get_current_user_optional
from ..services.users import audit as audit_log
from ..services.analytics import client_ip

router = APIRouter(prefix="/executor", tags=["executor"])

# Storage directory for uploaded scripts
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts_storage"
SCRIPTS_DIR.mkdir(exist_ok=True)


class ScriptArgument(BaseModel):
    """Defines an argument that a script accepts."""
    name: str
    type: Literal["string", "integer", "float", "boolean", "choice"] = "string"
    required: bool = False
    default: str | None = None
    description: str = ""
    choices: list[str] | None = None  # For 'choice' type


class ScriptCreate(BaseModel):
    """Schema for creating a new script."""
    name: str
    description: str = ""
    category: str = "general"
    arguments: list[ScriptArgument] = Field(default_factory=list)
    is_public: bool = True  # If False, only admins can see it


class ScriptVersionCreate(BaseModel):
    """Schema for uploading a new version of a script."""
    version: str  # e.g., "1.0.0", "2.1.3"
    code: str  # Python code
    changelog: str = ""


class ScriptInfo(BaseModel):
    """Information about a script."""
    id: int
    name: str
    description: str
    category: str
    arguments: list[ScriptArgument]
    is_public: bool
    created_at: str
    updated_at: str
    versions: list["ScriptVersionInfo"]
    latest_version: str | None = None


class ScriptVersionInfo(BaseModel):
    """Information about a specific script version."""
    id: int
    script_id: int
    version: str
    changelog: str
    created_at: str
    created_by: str | None = None


class ExecutionRequest(BaseModel):
    """Request to execute a script."""
    script_id: int
    version: str | None = None  # Use latest if not specified
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout: int = 30  # seconds


class ExecutionResult(BaseModel):
    """Result of a script execution."""
    execution_id: str
    script_name: str
    version: str
    status: Literal["success", "error", "timeout"]
    stdout: str
    stderr: str
    exit_code: int | None
    duration: float  # seconds
    created_at: str


class ExecutionHistoryItem(BaseModel):
    """Item in execution history."""
    execution_id: str
    script_name: str
    version: str
    status: str
    duration: float
    created_at: str
    executed_by: str | None = None


# In-memory storage (replace with database in production)
_scripts: dict[int, dict[str, Any]] = {}
_versions: dict[int, dict[int, dict[str, Any]]] = {}  # script_id -> {version_id -> version_data}
_executions: list[dict[str, Any]] = []
_script_counter = 0
_version_counter = 0


def _get_script(script_id: int, user_role: str | None) -> dict[str, Any] | None:
    """Get a script by ID, respecting visibility rules."""
    script = _scripts.get(script_id)
    if not script:
        return None
    # Non-admin users can only see public scripts
    if user_role != "admin" and not script.get("is_public", True):
        return None
    return script


@router.get("/scripts")
async def list_scripts(
    category: str | None = None,
    include_private: bool = False,
    user: dict | None = Depends(get_current_user_optional),
) -> list[ScriptInfo]:
    """List all available scripts."""
    user_role = user.get("role") if user else None
    
    result = []
    for script_id, script in _scripts.items():
        # Filter by visibility
        if user_role != "admin" and not script.get("is_public", True):
            continue
        # Filter by category
        if category and script.get("category") != category:
            continue
        
        versions = _versions.get(script_id, {})
        version_list = [
            ScriptVersionInfo(
                id=v["id"],
                script_id=v["script_id"],
                version=v["version"],
                changelog=v.get("changelog", ""),
                created_at=v["created_at"],
                created_by=v.get("created_by"),
            )
            for v in versions.values()
        ]
        
        result.append(ScriptInfo(
            id=script["id"],
            name=script["name"],
            description=script.get("description", ""),
            category=script.get("category", "general"),
            arguments=[ScriptArgument(**arg) for arg in script.get("arguments", [])],
            is_public=script.get("is_public", True),
            created_at=script["created_at"],
            updated_at=script["updated_at"],
            versions=version_list,
            latest_version=max((v["version"] for v in versions.values()), default=None) if versions else None,
        ))
    
    return result


@router.get("/scripts/{script_id}")
async def get_script(
    script_id: int,
    user: dict | None = Depends(get_current_user_optional),
) -> ScriptInfo:
    """Get details of a specific script."""
    script = _get_script(script_id, user.get("role") if user else None)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found or access denied")
    
    versions = _versions.get(script_id, {})
    version_list = [
        ScriptVersionInfo(
            id=v["id"],
            script_id=v["script_id"],
            version=v["version"],
            changelog=v.get("changelog", ""),
            created_at=v["created_at"],
            created_by=v.get("created_by"),
        )
        for v in versions.values()
    ]
    
    return ScriptInfo(
        id=script["id"],
        name=script["name"],
        description=script.get("description", ""),
        category=script.get("category", "general"),
        arguments=[ScriptArgument(**arg) for arg in script.get("arguments", [])],
        is_public=script.get("is_public", True),
        created_at=script["created_at"],
        updated_at=script["updated_at"],
        versions=version_list,
        latest_version=max((v["version"] for v in versions.values()), default=None) if versions else None,
    )


@router.post("/scripts", status_code=201)
async def create_script(
    body: ScriptCreate,
    request: Request,
    user: dict = Depends(require_admin),
) -> ScriptInfo:
    """Create a new script (admin only)."""
    global _script_counter
    
    now = datetime.utcnow().isoformat() + "Z"
    _script_counter += 1
    
    script_data = {
        "id": _script_counter,
        "name": body.name,
        "description": body.description,
        "category": body.category,
        "arguments": [arg.model_dump() for arg in body.arguments],
        "is_public": body.is_public,
        "created_at": now,
        "updated_at": now,
    }
    
    _scripts[_script_counter] = script_data
    _versions[_script_counter] = {}
    
    audit_log("script.created", actor=user, target=body.name, ip=client_ip(request))
    
    return ScriptInfo(
        id=script_data["id"],
        name=script_data["name"],
        description=script_data["description"],
        category=script_data["category"],
        arguments=[ScriptArgument(**arg) for arg in script_data["arguments"]],
        is_public=script_data["is_public"],
        created_at=script_data["created_at"],
        updated_at=script_data["updated_at"],
        versions=[],
        latest_version=None,
    )


@router.post("/scripts/{script_id}/versions", status_code=201)
async def upload_script_version(
    script_id: int,
    body: ScriptVersionCreate,
    request: Request,
    user: dict = Depends(require_admin),
) -> ScriptVersionInfo:
    """Upload a new version of a script (admin only)."""
    global _version_counter
    
    script = _get_script(script_id, "admin")
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")
    
    now = datetime.utcnow().isoformat() + "Z"
    _version_counter += 1
    
    version_data = {
        "id": _version_counter,
        "script_id": script_id,
        "version": body.version,
        "code": body.code,
        "changelog": body.changelog,
        "created_at": now,
        "created_by": user.get("username") or user.get("email"),
    }
    
    if script_id not in _versions:
        _versions[script_id] = {}
    
    _versions[script_id][_version_counter] = version_data
    _scripts[script_id]["updated_at"] = now
    
    audit_log("script.version_uploaded", actor=user, target=f"{script['name']} v{body.version}", ip=client_ip(request))
    
    return ScriptVersionInfo(
        id=version_data["id"],
        script_id=version_data["script_id"],
        version=version_data["version"],
        changelog=version_data["changelog"],
        created_at=version_data["created_at"],
        created_by=version_data["created_by"],
    )


@router.delete("/scripts/{script_id}")
async def delete_script(
    script_id: int,
    request: Request,
    user: dict = Depends(require_admin),
) -> dict:
    """Delete a script and all its versions (admin only)."""
    script = _get_script(script_id, "admin")
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")
    
    del _scripts[script_id]
    if script_id in _versions:
        del _versions[script_id]
    
    audit_log("script.deleted", actor=user, target=script["name"], ip=client_ip(request))
    
    return {"ok": True}


@router.post("/execute")
async def execute_script(
    body: ExecutionRequest,
    request: Request,
    user: dict | None = Depends(get_current_user_optional),
) -> ExecutionResult:
    """Execute a script with given arguments."""
    script = _get_script(body.script_id, user.get("role") if user else None)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found or access denied")
    
    # Get the requested version or latest
    versions = _versions.get(body.script_id, {})
    if not versions:
        raise HTTPException(status_code=404, detail="No versions available for this script")
    
    if body.version:
        version_data = next((v for v in versions.values() if v["version"] == body.version), None)
        if not version_data:
            raise HTTPException(status_code=404, detail=f"Version {body.version} not found")
    else:
        # Use latest version
        version_data = max(versions.values(), key=lambda v: v["created_at"])
    
    # Validate arguments against script definition
    arg_definitions = {arg["name"]: arg for arg in script.get("arguments", [])}
    validated_args = {}
    
    for arg_name, arg_value in body.arguments.items():
        if arg_name not in arg_definitions:
            continue  # Ignore unknown arguments
        
        arg_def = arg_definitions[arg_name]
        
        # Type validation
        try:
            if arg_def["type"] == "integer":
                validated_args[arg_name] = int(arg_value)
            elif arg_def["type"] == "float":
                validated_args[arg_name] = float(arg_value)
            elif arg_def["type"] == "boolean":
                validated_args[arg_name] = arg_value in (True, "true", "1", "yes")
            else:
                validated_args[arg_name] = str(arg_value)
        except (ValueError, TypeError) as e:
            raise HTTPException(status_code=400, detail=f"Invalid value for argument '{arg_name}': {e}")
        
        # Check required arguments
        for arg_def in script.get("arguments", []):
            if arg_def["required"] and arg_def["name"] not in body.arguments:
                raise HTTPException(status_code=400, detail=f"Missing required argument: {arg_def['name']}")
    
    # Apply defaults for missing optional arguments
    for arg_def in script.get("arguments", []):
        if arg_def["name"] not in validated_args and arg_def.get("default"):
            validated_args[arg_def["name"]] = arg_def["default"]
    
    # Execute the script
    execution_id = str(uuid.uuid4())
    start_time = datetime.utcnow()
    
    try:
        # Create a temporary file with the script code
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(version_data["code"])
            temp_script_path = f.name
        
        try:
            # Build command line arguments
            cmd_args = ["python3", temp_script_path]
            for arg_name, arg_value in validated_args.items():
                cmd_args.extend([f"--{arg_name}", str(arg_value)])
            
            # Run the script
            process = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *cmd_args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=5.0
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=body.timeout
                )
                status = "success" if process.returncode == 0 else "error"
                exit_code = process.returncode
            except asyncio.TimeoutError:
                process.kill()
                await process.communicate()
                status = "timeout"
                stdout = b""
                stderr = b"Execution timed out"
                exit_code = -1
            
        finally:
            # Clean up temp file
            import os
            os.unlink(temp_script_path)
        
        end_time = datetime.utcnow()
        duration = (end_time - start_time).total_seconds()
        
        result = ExecutionResult(
            execution_id=execution_id,
            script_name=script["name"],
            version=version_data["version"],
            status=status,
            stdout=stdout.decode() if stdout else "",
            stderr=stderr.decode() if stderr else "",
            exit_code=exit_code,
            duration=duration,
            created_at=end_time.isoformat() + "Z",
        )
        
        # Store execution in history
        _executions.append({
            "execution_id": execution_id,
            "script_id": script["id"],
            "script_name": script["name"],
            "version": version_data["version"],
            "status": status,
            "duration": duration,
            "created_at": result.created_at,
            "executed_by": user.get("username") if user else None,
            "arguments": validated_args,
        })
        
        return result
        
    except Exception as e:
        end_time = datetime.utcnow()
        duration = (end_time - start_time).total_seconds()
        
        result = ExecutionResult(
            execution_id=execution_id,
            script_name=script["name"],
            version=version_data["version"],
            status="error",
            stdout="",
            stderr=str(e),
            exit_code=-1,
            duration=duration,
            created_at=end_time.isoformat() + "Z",
        )
        
        return result


@router.get("/executions")
async def list_executions(
    script_id: int | None = None,
    limit: int = 50,
    user: dict = Depends(require_admin),
) -> list[ExecutionHistoryItem]:
    """List execution history (admin only)."""
    results = _executions.copy()
    
    if script_id:
        results = [e for e in results if e.get("script_id") == script_id]
    
    # Sort by created_at descending
    results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    
    return [
        ExecutionHistoryItem(
            execution_id=e["execution_id"],
            script_name=e["script_name"],
            version=e["version"],
            status=e["status"],
            duration=e["duration"],
            created_at=e["created_at"],
            executed_by=e.get("executed_by"),
        )
        for e in results[:limit]
    ]


@router.get("/executions/{execution_id}")
async def get_execution_result(
    execution_id: str,
    user: dict | None = Depends(get_current_user_optional),
) -> ExecutionResult | None:
    """Get details of a specific execution."""
    # Find the execution
    execution = next((e for e in _executions if e["execution_id"] == execution_id), None)
    if not execution:
        return None
    
    # Users can only see their own executions unless they're admin
    if user is None or (user.get("role") != "admin" and execution.get("executed_by") != user.get("username")):
        raise HTTPException(status_code=403, detail="Access denied")
    
    return ExecutionResult(
        execution_id=execution["execution_id"],
        script_name=execution["script_name"],
        version=execution["version"],
        status=execution["status"],
        stdout=execution.get("stdout", ""),
        stderr=execution.get("stderr", ""),
        exit_code=execution.get("exit_code"),
        duration=execution["duration"],
        created_at=execution["created_at"],
    )


@router.get("/categories")
async def list_categories(
    user: dict | None = Depends(get_current_user_optional),
) -> list[str]:
    """List all script categories."""
    user_role = user.get("role") if user else None
    
    categories = set()
    for script in _scripts.values():
        if user_role != "admin" and not script.get("is_public", True):
            continue
        categories.add(script.get("category", "general"))
    
    return sorted(categories)
