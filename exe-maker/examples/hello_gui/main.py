"""Example GUI app — a tiny tkinter window. Reads a data file next to itself
and shows what the DECINT launcher exported about the license."""
import json
import os
import tkinter as tk
from tkinter import ttk

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    with open(os.path.join(HERE, "assets", "greeting.json"), encoding="utf-8") as fh:
        greeting = json.load(fh)["text"]
    root = tk.Tk()
    root.title("Hello from DECINT")
    root.geometry("420x220")
    ttk.Label(root, text=greeting, font=("Segoe UI", 16)).pack(pady=20)
    mode = os.environ.get("DECINT_LICENSE_MODE", "unlicensed build")
    who = os.environ.get("DECINT_LICENSE_CUSTOMER", "")
    ttk.Label(root, text=f"license: {mode} {who}").pack()
    ttk.Button(root, text="Close", command=root.destroy).pack(pady=20)
    root.mainloop()


if __name__ == "__main__":
    main()
