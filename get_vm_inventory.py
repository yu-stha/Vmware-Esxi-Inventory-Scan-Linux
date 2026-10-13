#!/usr/bin/env python3
"""ESXi VM inventory for Linux/macOS (Python 3.6+).

Run directly from a URL:   curl -fsSL <url-to-this-file> | python3 -
Or as a local file:        python3 get_vm_inventory.py

Prompts are read from the terminal (/dev/tty), so they work even when the
script itself is piped into python3.
"""

import csv
import getpass
import glob
import os
import re
import socket
import ssl
import subprocess
import sys
from datetime import datetime

FIELDS = [
    "ESXi", "Name", "PowerState", "NumCpu", "MemoryGB", "IP", "UUID",
    "DiskCount", "DiskAllocatedGB", "DatastoreUsedGB",
    "Datastore", "Network", "MAC", "NicType",
]
DISPLAY = ["ESXi", "Name", "PowerState", "DiskAllocatedGB", "Datastore", "Network", "IP"]


# ---------------------------------------------------------------- helpers
def ask(prompt):
    """Read a line from the terminal even if stdin is a pipe."""
    try:
        # Separate handles: a single "r+" text handle fails on a tty (not seekable)
        with open("/dev/tty", "w") as out, open("/dev/tty", "r") as inp:
            out.write(prompt)
            out.flush()
            return inp.readline().strip()
    except OSError:
        pass
    try:
        return input(prompt).strip()
    except EOFError:
        sys.exit("\nNo terminal available for prompts. Run it from an interactive shell.")


def prompt_creds(label):
    print(label)
    user = ask("  Username: ")
    if not user:
        return None
    pwd = getpass.getpass("  Password: ")
    return user, pwd


def ensure_pyvmomi():
    """Import pyVmomi, installing it into a private venv if it is missing."""
    try:
        import pyVmomi  # noqa: F401
        return
    except ImportError:
        pass

    venv_dir = os.path.expanduser("~/.cache/vm-inventory-venv")
    py = os.path.join(venv_dir, "bin", "python")
    print("pyvmomi not found. Installing it into %s (one-time)..." % venv_dir)
    try:
        pip_ok = os.path.exists(py) and subprocess.call(
            [py, "-m", "pip", "--version"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0
        if not pip_ok:  # missing, or half-created by an earlier failed attempt
            subprocess.check_call([sys.executable, "-m", "venv", "--clear", venv_dir])
        subprocess.check_call([py, "-m", "pip", "install", "--quiet", "pyvmomi"])
    except (subprocess.CalledProcessError, OSError) as exc:
        sys.exit(
            "Could not install pyvmomi automatically (%s).\n"
            "On Debian/Ubuntu install the venv module first:  sudo apt install python3-venv\n"
            "(if apt asks for a versioned package, use e.g. python3.14-venv; note: venv, not env)\n"
            "Or install pyvmomi from apt:  sudo apt install python3-pyvmomi" % exc
        )

    for site in glob.glob(os.path.join(venv_dir, "lib", "python3*", "site-packages")):
        sys.path.insert(0, site)
    try:
        import pyVmomi  # noqa: F401
    except ImportError:
        sys.exit("pyvmomi was installed but could not be imported. Try: pip install --user pyvmomi")


def fmt_num(value):
    value = round(value, 2)
    return int(value) if float(value).is_integer() else value


# ------------------------------------------------------------- collection
def vm_row(vm, host, vim, ds_cache):
    cfg = vm.config
    summary = vm.summary
    disks, nics = [], []

    if cfg:
        for dev in cfg.hardware.device:
            if isinstance(dev, vim.vm.device.VirtualDisk):
                disks.append(dev)
            elif isinstance(dev, vim.vm.device.VirtualEthernetCard):
                nics.append(dev)
        name, cpu, mem_mb, uuid = cfg.name, cfg.hardware.numCPU, cfg.hardware.memoryMB, cfg.instanceUuid
    else:  # orphaned / inaccessible VM
        sc = summary.config
        name, cpu, mem_mb, uuid = sc.name, sc.numCpu, sc.memorySizeMB, sc.instanceUuid

    alloc_bytes = 0
    for d in disks:
        size = getattr(d, "capacityInBytes", None) or (d.capacityInKB or 0) * 1024
        alloc_bytes += size

    committed = summary.storage.committed if summary.storage else 0

    # Port group names for distributed switches
    pg_names = {}
    for net in (vm.network or []):
        if isinstance(net, vim.dvs.DistributedVirtualPortgroup):
            pg_names[net.key] = net.name

    networks, macs, types = [], [], []
    for nic in nics:
        backing = nic.backing
        if isinstance(backing, vim.vm.device.VirtualEthernetCard.NetworkBackingInfo):
            networks.append(backing.deviceName)
        elif isinstance(backing, vim.vm.device.VirtualEthernetCard.DistributedVirtualPortBackingInfo):
            key = backing.port.portgroupKey
            networks.append(pg_names.get(key, key))
        macs.append(nic.macAddress or "")
        types.append(type(nic).__name__.split(".")[-1].replace("Virtual", "", 1))

    ips = []
    if vm.guest:
        for gnic in (vm.guest.net or []):
            for ip in (gnic.ipAddress or []):
                if ip not in ips:
                    ips.append(ip)
        if not ips and vm.guest.ipAddress:
            ips.append(vm.guest.ipAddress)

    ds_names = []
    for ds in (vm.datastore or []):
        key = ds._moId
        if key not in ds_cache:
            ds_cache[key] = ds.name
        ds_names.append(ds_cache[key])

    state = str(vm.runtime.powerState)
    return {
        "ESXi": host,
        "Name": name,
        "PowerState": state[:1].upper() + state[1:],
        "NumCpu": cpu,
        "MemoryGB": fmt_num((mem_mb or 0) / 1024.0),
        "IP": ",".join(ips),
        "UUID": uuid,
        "DiskCount": len(disks),
        "DiskAllocatedGB": fmt_num(alloc_bytes / 1024.0 ** 3),
        "DatastoreUsedGB": fmt_num((committed or 0) / 1024.0 ** 3),
        "Datastore": ",".join(ds_names),
        "Network": ",".join(networks),
        "MAC": ",".join(macs),
        "NicType": ",".join(types),
    }


def collect_host(content, host, vim):
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
    try:
        vms = list(view.view)
    finally:
        view.Destroy()

    print("%s : %d VMs" % (host, len(vms)))
    ds_cache, rows = {}, []
    for vm in vms:
        try:
            rows.append(vm_row(vm, host, vim, ds_cache))
        except Exception as exc:  # keep going if one VM is unreadable
            print("WARNING: could not read a VM on %s: %s" % (host, exc), file=sys.stderr)
    return rows


# ---------------------------------------------------------------- output
def print_table(rows, cols):
    widths = [max(len(c), *(len(str(r[c])) for r in rows)) for c in cols]

    def line(vals):
        return "  ".join(str(v).ljust(w) for v, w in zip(vals, widths))

    print(line(cols))
    print(line(["-" * w for w in widths]))
    for r in rows:
        print(line([r[c] for c in cols]))


# ------------------------------------------------------------------ main
def main():
    ensure_pyvmomi()
    from pyVim.connect import SmartConnect, Disconnect
    from pyVmomi import vim

    socket.setdefaulttimeout(60)

    raw = ask("Enter ESXi IPs/hostnames (separate multiple with commas): ")
    hosts = list(dict.fromkeys(h for h in re.split(r"[,;\s]+", raw) if h))
    if not hosts:
        sys.exit("No hosts entered. Exiting.")

    use_shared = True
    if len(hosts) > 1:
        if ask("Use the same credentials for all hosts? (Y/N, default Y): ").lower().startswith("n"):
            use_shared = False

    shared = None
    if use_shared:
        shared = prompt_creds("ESXi credentials (use a read-only account):")
        if not shared:
            sys.exit("No username entered. Exiting.")

    # Self-signed ESXi certificates: certificate validation is disabled.
    ctx = ssl._create_unverified_context()
    inventory = []

    for host in hosts:
        creds = shared if use_shared else prompt_creds("Credentials for %s:" % host)
        si = None
        while si is None and creds:
            try:
                si = SmartConnect(host=host, user=creds[0], pwd=creds[1], sslContext=ctx)
            except Exception as exc:
                msg = getattr(exc, "msg", None) or str(exc)
                print("WARNING: Cannot connect to %s: %s" % (host, msg), file=sys.stderr)
                if ask("Retry %s with different credentials? (Y/N): " % host).lower().startswith("y"):
                    creds = prompt_creds("Credentials for %s:" % host)
                else:
                    break
        if si is None:
            continue

        try:
            inventory.extend(collect_host(si.RetrieveContent(), host, vim))
        except Exception as exc:
            print("WARNING: error while collecting from %s: %s" % (host, exc), file=sys.stderr)
        finally:
            Disconnect(si)

    if not inventory:
        sys.exit("No VMs collected.")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(os.getcwd(), "vm-inventory-%s.csv" % stamp)
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(inventory)

    print()
    print_table(sorted(inventory, key=lambda r: (r["ESXi"], str(r["Name"]).lower())), DISPLAY)

    print()
    counts = {}
    for r in inventory:
        counts[r["ESXi"]] = counts.get(r["ESXi"], 0) + 1
    print_table([{"ESXi": h, "Count": c} for h, c in counts.items()], ["ESXi", "Count"])

    print("\nSaved: %s" % path)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nCancelled.")
