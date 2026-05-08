# Docker

Multi-arch image (`linux/amd64` + `linux/arm64`) is published to GitHub Container Registry on every push to `main` and on every `v*` tag.

```
ghcr.io/moazzamsameer/sentinel-dns:latest      # main branch
ghcr.io/moazzamsameer/sentinel-dns:v0.1.0      # tagged release
```

The arm64 image is the prosumer/Pi target — same code, same dependencies, no pre-trained classifier model bundled (more on that below).

## Three deployment patterns

Each pattern solves a different "how do I get a DNS resolver listening on port 53" problem. Pick the one that matches your network setup.

### 1. Unprivileged port (5354) — quickest test

```bash
docker run --rm -p 5354:5354/udp ghcr.io/moazzamsameer/sentinel-dns:latest \
    --listen-host 0.0.0.0 \
    --blocklist-url https://urlhaus.abuse.ch/downloads/hostfile/ \
    --enforce

dig @127.0.0.1 -p 5354 google.com +short
# → 142.251.223.142
```

The container's UDP/5354 is mapped to the host's UDP/5354. Doesn't replace your system DNS — just lets you hit the resolver explicitly.

### 2. Port-mapped to host's 53 — replaces system DNS

```bash
docker run -d --restart unless-stopped \
    -p 53:5354/udp \
    --name sentinel-dns \
    ghcr.io/moazzamsameer/sentinel-dns:latest \
    --listen-host 0.0.0.0 \
    --blocklist-url https://urlhaus.abuse.ch/downloads/hostfile/ \
    --enforce

# In your network or device DNS settings, set the resolver to the host's IP.
# Or, on the same machine, point /etc/resolv.conf at 127.0.0.1.
```

The container itself stays unprivileged — Docker's port-mapping handles the privileged-port traffic forwarding. **Recommended for most setups.**

### 3. Host networking + capability — minimum overhead, most exposure

```bash
docker run -d --restart unless-stopped \
    --network host \
    --cap-add NET_BIND_SERVICE \
    --name sentinel-dns \
    ghcr.io/moazzamsameer/sentinel-dns:latest \
    --listen-host 0.0.0.0 --listen-port 53 \
    --blocklist-url https://urlhaus.abuse.ch/downloads/hostfile/ \
    --enforce
```

`--network host` means the container shares the host's network namespace (no port-mapping layer). `--cap-add NET_BIND_SERVICE` lets the non-root user inside bind UDP/53. Slightly faster and slightly more visible to other host processes than option (2). Use only if you understand both implications.

> Don't pass `--user 0`. The image's default non-root user + `NET_BIND_SERVICE` is the safer combination of "binds privileged port" and "can't write to /".

## With a TOML config

The image has no config baked in. Mount yours:

```bash
docker run -d --restart unless-stopped \
    -p 53:5354/udp \
    -v $(pwd)/sentinel-dns.toml:/home/sentinel/sentinel-dns.toml:ro \
    -v sentinel-data:/home/sentinel/data \
    ghcr.io/moazzamsameer/sentinel-dns:latest \
    --config /home/sentinel/sentinel-dns.toml
```

The `sentinel-data` named volume is for the SQLite query log (if `log_path` is set in the TOML to a path under `/home/sentinel/data`). Persist across container restarts.

## With a trained classifier

The image **does not bundle the trained model.** Training pulls URLhaus + Tranco at runtime; baking that into the image would mean the image is only as fresh as the day it was built, and the data files are 10× the size of the rest of the image combined.

Train on the host, mount the result:

```bash
# On the host
git clone https://github.com/MoazzamSameer/sentinel-dns.git
cd sentinel-dns
mkdir -p data
curl -sS -o data/urlhaus_hosts.txt https://urlhaus.abuse.ch/downloads/hostfile/
curl -sS -L -o /tmp/tranco.zip https://tranco-list.eu/top-1m.csv.zip
unzip -p /tmp/tranco.zip top-1m.csv | head -100000 > data/tranco_top100k.csv

python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python scripts/train_classifier.py
# → models/classifier_v0.joblib

# Mount it into the container
docker run -d --restart unless-stopped \
    -p 53:5354/udp \
    -v $(pwd)/models:/home/sentinel/models:ro \
    ghcr.io/moazzamsameer/sentinel-dns:latest \
    --listen-host 0.0.0.0 \
    --model-path /home/sentinel/models/classifier_v0.joblib \
    --blocklist-url https://urlhaus.abuse.ch/downloads/hostfile/ \
    --enforce
```

A future task may ship a separate "trainer" image that produces the model and lets the resolver image stay lean.

## Inspecting the running container

```bash
# Live tail (mount the SQLite log volume, point tail at it)
docker exec -it sentinel-dns sentinel-dns tail -f --log-path /home/sentinel/data/sentinel.db

# Explain a single domain
docker exec -it sentinel-dns sentinel-dns explain example.com \
    --log-path /home/sentinel/data/sentinel.db

# Or just run sentinel-dns log lines
docker logs -f sentinel-dns
```

## Healthcheck

The image ships a Docker `HEALTHCHECK` that sends a UDP DNS query for `example.com` to localhost and exits 0 only if the resolver answers. Plays nicely with `--restart unless-stopped` and orchestrators that use Docker's health status.

## Multi-arch detail

Built via `docker/build-push-action@v6` with QEMU emulation for arm64 on the amd64 runner. `docker pull` automatically picks the right image for your platform.

If you want to verify which arch you got:

```bash
docker image inspect ghcr.io/moazzamsameer/sentinel-dns:latest --format '{{.Architecture}}/{{.Os}}'
# → arm64/linux on a Pi or M-series Mac
# → amd64/linux on x86_64
```

## Caveats

1. **No local Docker verification on this developer machine.** This PR's Dockerfile and workflow were written without local `docker build`. The PR check workflow runs the build + a smoke test on every PR — that's the verification gate. If you're cloning to test, install Docker Desktop or Colima first.
2. **`-p 53:5354/udp` requires root on the *host* (or a CAP_NET_BIND_SERVICE on dockerd).** Most Docker installs already grant this; documented for completeness.
3. **GHCR is the only registry.** No Docker Hub mirror. Anonymous pulls work; for higher rate limits, log into GHCR with a personal access token (any classification, no special scope needed for public repos).
4. **No image-signing / SBOMs / provenance attestations** in this PR. `docker/build-push-action` supports `provenance:` and `sbom:` flags; could turn on later. Out of v0.1 scope.
5. **arm64 builds on emulation, not native.** GitHub Actions runners are amd64-only on the free tier; arm64 build runs through QEMU. This makes arm64 builds 3–5× slower than amd64 in CI but doesn't affect the runtime image. If build time becomes painful, switch to a self-hosted arm64 runner or to GitHub's arm64 hosted runners (paid).
6. **Default config is "no inline tier."** Image with no flags runs as a bare forwarder to 1.1.1.1 over UDP — same as `sentinel-dns` with no flags. Useful for "is this image even working" but not what you want in production. Always pass `--blocklist-url` and/or `--model-path` when running for real.

## What this unblocks

- Anyone with Docker can run `sentinel-dns` in 30 seconds without a Python toolchain.
- Pi 4 / homelab users can drop the arm64 image into their existing container infrastructure (docker-compose, Portainer, etc.) without building from source.
- The eventual v0.1 release announcement will have a `docker run` one-liner alongside the `pip install`.
