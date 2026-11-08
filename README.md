# LanDrop 🚀 — P2P Distributed Virtual Storage & LAN Drive

LanDrop turns connected PCs across your local network and Tailscale into a **unified Peer-to-Peer Virtual Drive mounted at `Z:`**.

---

## 🌟 How It Works

1. **Unified `Z:` Drive on All Connected PCs**:
   - Each machine running LanDrop automatically mounts a virtual Windows drive at **`Z:`** (or an available drive letter).
   - Users on both PC A and PC B open Windows File Explorer to `Z:` and see the **exact same shared virtual filesystem**.

2. **Physically Distributed Virtual Storage Pool**:
   - Files placed in `Z:` are stored in the local physical storage pool (`~/.landrop/storage/`) of the machine that uploaded them.
   - **Configurable Virtual Storage Quota:** In the app, each user sets their storage contribution (e.g. 10 GB, 50 GB, 100 GB). Windows Explorer's drive bar on `Z:` accurately displays this capacity!

3. **Peer-to-Peer Mapping Tables**:
   - Every node maintains a synchronized **Mapping Table** (catalog) tracking all virtual files: path, size, hash, and which peer physically holds each file.
   - Updates sync in real-time across both **Local LAN** and **Tailscale VPN** mesh.

4. **Place & Collect / Stream**:
   - **Placing Files:** Drop any file into `Z:`. The file is saved locally, and your mapping table broadcasts the new file to all peers.
   - **Instant Streaming:** Other PCs see the file immediately in `Z:` and can stream videos or audio (in VLC, mpv, or Windows Media Player) via HTTP Range requests without downloading the entire file first.
   - **Collecting Files:** Users can drag the file from `Z:` to their desktop or click **"Collect / Download to Local"** in the app to fetch a copy.

---

## ⚡ Quick Start (Standalone EXE)

👉 **Double-click [`LanDrop.exe`](file:///c:/Users/tushe/Desktop/Projects/LanDrop/LanDrop.exe) to start.**

- No Python or command line needed.
- Automatically launches in windowed mode (no console popups).
- Mounts virtual drive to `Z:` and docks into the system tray.

---

## 🖥️ Application Features

### 💽 Virtual Drive (Z:) Tab
- **Mount Status**: Displays current mount status (e.g. `● Mounted at Z:`) with 1-click **Open Z: Drive**, **Remount**, or **Unmount**.
- **Virtual Storage Pool & Quota**: Set your storage allocation in GB (e.g. 20 GB) with a live visual capacity progress meter.
- **Shared Virtual Files Table**: Browse all files across the P2P cluster, showing which PC physically holds them, their size, and actions:
  - **Open in Explorer / Stream**: Plays or views the file directly.
  - **Collect / Download to Local**: Downloads remote-held files to any chosen local folder.

### 👥 Discovered Peers Tab
- Sniffs all PCs on your LAN and Tailscale running LanDrop.
- **Sync Mapping Tables**: Manually force a two-way catalog exchange.
- **Connect by IP / Tailscale...**: Connect to any remote peer by IP or Tailscale domain.

### 🌐 Network & Tailscale Settings Tab
- Shows both **Local LAN WebDAV URL** (`http://192.168.x.x:8080/`) and **Tailscale VPN URL** (`http://100.x.y.z:8080/`).
- Toggle Read-Only mode or change port/device name.

---

## 🧪 Testing

Run the automated test suite:
```bash
python -m unittest discover -s tests -v
```
Tests verify:
- Mapping Table versioning, conflict resolution, and tombstone deletes.
- P2P sync engine and raw chunk/range streaming between nodes.
- Quota calculations and virtual directory trees.
