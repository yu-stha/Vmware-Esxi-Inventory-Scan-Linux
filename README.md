# VMware ESXi Inventory Scan (Linux)

A Python script that connects to one or more standalone ESXi hosts, pulls every VM on each host, and saves the result to a timestamped CSV. Use it to keep an asset inventory current when VMs are frequently added or deleted.

This is the Linux/macOS counterpart of the PowerShell version (`Vmware-Esxi-Inventory-Scan-windows`). Both produce the same CSV columns.

## What it collects

For each VM:

| Column | Description |
|---|---|
| ESXi | Host the VM was read from |
| Name | VM name |
| PowerState | PoweredOn / PoweredOff / Suspended |
| NumCpu, MemoryGB | Allocated vCPU and RAM |
| IP | Guest IP addresses, IPv6 included (empty if VMware Tools is not running) |
| UUID | VM instance UUID (stable key; names can be reused) |
| DiskCount | Number of virtual disks |
| DiskAllocatedGB | Sum of all virtual disk sizes |
| DatastoreUsedGB | Committed space the VM uses on the datastore (includes snapshots and swap) |
| Datastore | Datastore(s) holding the VM |
| Network, MAC, NicType | Port group, MAC address and adapter type per NIC, in NIC order |

It also prints a per-host VM count and a summary table.

## Requirements

- Linux or macOS with `python3` (3.6+)
- Network access to the ESXi hosts (HTTPS, port 443)
- `pyvmomi`. If it is missing, the script installs it into a private virtual environment at `~/.cache/vm-inventory-venv` (one-time, needs internet access to PyPI).
- An ESXi account. A **read-only** account is enough and recommended.

On Debian/Ubuntu, the automatic install needs the venv module:

```bash
sudo apt install python3-venv       # or the versioned package, e.g. python3.14-venv
```

Or skip the venv entirely and install pyvmomi from apt:

```bash
sudo apt install python3-pyvmomi
```

## Usage

### Run directly from GitHub

```bash
curl -fsSL https://raw.githubusercontent.com/yu-stha/Vmware-Esxi-Inventory-Scan-Linux/main/get_vm_inventory.py | python3 -
```

The prompts read from the terminal (`/dev/tty`), so they work even though the script arrives through a pipe. This executes whatever is at that URL: review the script first, and pin the URL to a commit hash if you share it with others.

### Run the downloaded file

```bash
python3 get_vm_inventory.py
```

### What you will be asked

1. **ESXi IPs or hostnames**, comma-separated, for example `10.10.101.11,10.10.101.12`.
2. **Same credentials for all hosts?** (only asked when you enter more than one host). Answer `N` to be prompted per host.
3. **Username and password.** The password is hidden as you type. If a login fails, you are offered a retry with different credentials for that host.

## Output

A file named `vm-inventory-<yyyyMMdd-HHmmss>.csv` is written to the folder you ran the script from, for example `vm-inventory-20261012-153045.csv`. The full path is printed at the end.

### Viewing the CSV

```bash
csvlook vm-inventory-*.csv | less -S          # sudo apt install csvkit
libreoffice --calc vm-inventory-*.csv         # sudo apt install libreoffice-calc
```

In LibreOffice's import dialog, keep the separator as comma and the text delimiter as `"`. Several columns hold comma-separated lists inside quotes.

## Security notes

- No password is stored or hardcoded. Credentials are prompted for and kept in memory only.
- Use a read-only ESXi account.
- TLS certificate validation is disabled, because ESXi hosts use self-signed certificates by default. Only scan hosts on networks you trust.
- **The output CSV contains internal IPs, MAC addresses and VM names. Do not commit it to Git.** Add this to `.gitignore`:

  ```
  vm-inventory-*
  ```

- Repeated failed logins can lock an ESXi account (5 failures for about 15 minutes by default). Check the password before retrying more than once.

## Troubleshooting

| Problem | Fix |
|---|---|
| `ensurepip is not available` / venv creation fails | `sudo apt install python3-venv` (or `python3.X-venv`). Note: venv, not env |
| `No module named pip` from the installer | A half-built venv is left over. Run `rm -rf ~/.cache/vm-inventory-venv` and rerun |
| `pip install --user` refuses (externally-managed-environment) | Expected on recent Debian/Ubuntu. Use the venv route or `sudo apt install python3-pyvmomi` |
| `Cannot connect to <host>` | Check the IP, port 443 reachability, and the credentials |
| Empty `IP` column | VMware Tools is not running in that VM. Use `MAC` and `Network` instead |
| 404 when using the curl one-liner | The repo is private or the URL is wrong. A plain `curl` only works on public repos |

## Limitations

- Standalone ESXi hosts. With vCenter, a single connection to vCenter is a better approach.
- Templates and powered-off VMs are included in the output.
- Orphaned or inaccessible VMs still get a row, but their disk and NIC fields are empty because the host cannot read their config.
