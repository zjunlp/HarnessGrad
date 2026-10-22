# Moving Docker's data root, and why we have not done it yet

**Status: attempted 2026-10-06, and the first attempt was wrong in an instructive way. Read
"Which directory actually holds the images" before doing anything.**

## Which directory actually holds the images — **read this first**

`docker info` on this machine reports:

```
Storage Driver: overlayfs
driver-type: io.containerd.snapshotter.v1
```

That is the **containerd image store**, not the classic `overlay2` graph driver, and it moves
the answer to "where are the images":

| path | what is in it | measured |
|---|---|---|
| `/var/lib/docker` | metadata, container state, volumes, BuildKit cache | **~4 GB** |
| `/var/lib/containerd` | **the image content — layers and snapshots** | **~104 GB** |

`docker system df` reports 103.8 GB of images and `/var/lib/docker` is 4.0 GB, which is the
contradiction that gives it away. **Moving `/var/lib/docker` alone frees nothing and gains
nothing.** Measured 2026-10-06: an `rsync` of `/var/lib/docker` completed with 4,053,549,043
bytes over 89,158 files, which is the whole directory.

So a working move is **two moves**, and the second one is the one that matters. `containerd`
keeps its root in `/etc/containerd/config.toml` (`root = ...`, unset by default), **not** in
Docker's `daemon.json`.

The other trap the first attempt hit: `BuildKit` keeps **live** state under
`/var/lib/docker/buildkit`, so a copy made while the daemon runs is immediately stale. The
dry run said so -- four `>f.st......` lines, all under `buildkit/` -- and that is exactly what
step 1 is for.

## The situation, measured 2026-10-06

```
nvme0n1  1.8T  SSD   ├─ nvme0n1p2  ext4  /          1.8T total, 125G free (93% used)
sda      7.3T  HDD   └─ ext4        /mnt/8t        7.3T total, 948G free
sdb     18.2T  HDD   └─ sdb1 ext4   /mnt/20t      19T  total, 7.1T free
```

* `docker info` → **Docker Root Dir: `/var/lib/docker`**, which is on `/` — the only SSD.
* `docker system df` → **125 images, 102.2 GB**, 9.4 GB of it reclaimable.
* Only three filesystems exist. **There is no second SSD.** The one large filesystem is
  `/mnt/20t`, and it is **rotational** (`lsblk` reports `ROTA=1` for `sdb`).
* `/mnt/20t` is mounted `rw,nosuid,nodev,relatime` — **no `noexec`**, which matters: Docker
  needs to execute from its data root, and a `noexec` mount would break it in a way whose
  error message does not name the mount option.
* The two long-lived containers that a daemon restart interrupts, `memos-neo4j` (up 3 months)
  and `ael-searxng` (up 6 months), both carry `RestartPolicy: unless-stopped`, so they return
  by themselves once the daemon is back.

## The tradeoff, stated before the commands

Moving the data root to `/mnt/20t` buys ~7 TB and costs **I/O speed**. Overlayfs, image
unpacking and `docker commit` are all many-small-files workloads, and they are exactly the
workloads a mechanical disk is worst at. Every platform run does both: it unpacks a task image
per task and commits a snapshot per task. The honest expectation is that container start and
snapshot time get noticeably worse, which would show up in this platform as **more timeouts
counted as harness failures** — a measurement artefact, not a harness that got worse.

**So: do not move it yet.** The batch of datasets currently being adapted — ALFWorld,
ScienceWorld, Jericho, BabyAI, WebShop, SpreadsheetBench, API-Bank, BFCL, τ²-bench, the QA sets
— needs **no Docker at all**. Terminal-Bench is already adapted and its images are present.
The move is for the day we want SWE-bench (hundreds of GB), Cybench or Commit0, and it should
be made deliberately then, with the speed cost measured rather than assumed.

Before reaching for the HDD, two cheaper things are worth trying first: `docker system prune`
reclaims ~9.4 GB today, and `/` hides roughly 1.4 TB that an unprivileged `du` cannot see
(other users' homes, and `/var/lib/docker` itself). `sudo du -xh --max-depth=1 / | sort -rh`
answers where it went, and something in there may be dead weight rather than something to
work around.

## The procedure, for when we do want it

Every command below is destructive from step 2 onward. Read the whole thing first.

```sh
# 0. Know what you are about to move, and that you can copy it.
sudo du -sh /var/lib/docker                 # the number you must see again in step 3
sudo df -h /mnt/20t                         # must exceed it with room to grow
sudo docker ps -a --format '{{.Names}}\t{{.Status}}'   # note what should come back

# 1. Stop the daemon. Containers stop with it.
sudo systemctl stop docker.socket docker docker.service

# 2. Copy, do not move. `-aHAX` keeps hard links, xattrs and ACLs; `--numeric-ids` keeps
#    ownership when the uid does not exist on the reading side; `-S` handles sparse files.
#    The trailing slashes matter: this copies the *contents* of /var/lib/docker.
sudo mkdir -p /mnt/20t/docker
sudo rsync -aHAXS --numeric-ids --info=progress2 /var/lib/docker/ /mnt/20t/docker/

# 3. Verify BEFORE switching. Two numbers that must agree, and no error output.
sudo du -sh /mnt/20t/docker
sudo rsync -aHAXS --numeric-ids --dry-run --itemize-changes /var/lib/docker/ /mnt/20t/docker/ | head
#    The dry run printing nothing means the copy is complete. Do not skip this: rsync
#    exiting 0 on a partial copy is not a thing, but reading the wrong source with a
#    trailing slash is, and the dry run is what catches it.

# 3b. And copy the part that actually holds the images. This is the 104 GB one.
sudo mkdir -p /mnt/20t/containerd
sudo rsync -aHAXS --numeric-ids --info=progress2 /var/lib/containerd/ /mnt/20t/containerd/
sudo rsync -aHAXS --numeric-ids --dry-run --itemize-changes /var/lib/containerd/ /mnt/20t/containerd/ | head

# 4. Point BOTH at it. Two files, because the image store is containerd's and the rest is
#    Docker's -- a move that edits only one of them looks like it worked and frees nothing.
#    (Measured: `daemon.json` alone leaves `docker info` reporting the old root and the disk
#    exactly as full as before.)
sudo cp /etc/docker/daemon.json /etc/docker/daemon.json.bak
sudo tee /etc/docker/daemon.json >/dev/null <<'JSON'
{
  "data-root": "/mnt/20t/docker",
  "registry-mirrors": [
    "https://docker.1ms.run",
    "https://hub.rat.dev",
    "https://docker.1panel.live"
  ]
}
JSON

sudo cp /etc/containerd/config.toml /etc/containerd/config.toml.bak
# `root` is unset in the shipped file, so this appends it under [plugins] is NOT right --
# it belongs at the top level of the TOML. Verify with:
#   grep -n '^root' /etc/containerd/config.toml
# and add exactly one top-level line:
sudo sed -i '1i root = "/mnt/20t/containerd"' /etc/containerd/config.toml
sudo grep -n '^root' /etc/containerd/config.toml

# 5. Make the daemon refuse to start if /mnt/20t is not mounted. **Without this, a boot where
#    the mount is late or missing starts Docker against an empty directory and every image
#    appears to be gone** -- they are on the disk, but the daemon is looking somewhere else,
#    and the first symptom is a task image that "does not exist".
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/require-20t.conf >/dev/null <<'CONF'
[Unit]
RequiresMountsFor=/mnt/20t
CONF

# 6. Start, and check all three things that could be wrong.
sudo systemctl daemon-reload
sudo systemctl start containerd docker
docker info --format '{{.DockerRootDir}}'        # must print /mnt/20t/docker
docker images | wc -l                            # must match the count from step 0 -- 200 on
                                                 # 2026-10-06, so `docker images | wc -l`
                                                 # printing 201 is the header, not an image
df -h /                                         # the point of all this: free space must go UP
docker ps --format '{{.Names}}'                  # the unless-stopped ones should be back

# 7. Only after a few days of working runs:
# sudo rm -rf /var/lib/docker
```

## Rollback

Any time before step 6, nothing has changed that matters: the real data root is still
`/var/lib/docker` and the daemon is pointed at it. To undo the switch:

```sh
sudo cp /etc/docker/daemon.json.bak /etc/docker/daemon.json
sudo cp /etc/containerd/config.toml.bak /etc/containerd/config.toml
sudo rm -f /etc/systemd/system/docker.service.d/require-20t.conf
sudo systemctl daemon-reload && sudo systemctl restart containerd docker
```

After step 6 the old copy is still intact until step 7, so the rollback above also works then.
The one state with no rollback is `rm -rf /var/lib/docker` before the new root has been proven
by real runs.
