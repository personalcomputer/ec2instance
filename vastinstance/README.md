# ec2instance-vast

Launch new vast.ai instances easily and quickly with one command. Inspired by [ec2instance](https://github.com/personalcomputer/ec2instance).

## Quick Start

```bash
# Set your vast.ai API key (one-time). Also used by the vastai CLI.
echo -n "your-api-key" > ~/.config/vastai/vast_api_key
# or: export VAST_API_KEY="your-api-key"

# Find an offer ID
vastai search offers

# Launch an instance (defaults: nvidia-cuda template)
vastinstance -t 123456789

# Use a different image template
vastinstance -t 123456789 -i linux-desktop-container
vastinstance -t 123456789 -i ubuntu-desktop-vm

# Override disk size
vastinstance -t 123456789 --disk 64

# Detach mode - output JSON metadata and don't open shell
vastinstance -t 123456789 --detach
```

## Features

- **One-command launch**: Just run `vastinstance -t <offer-id>` and get a shell on a fresh instance.
- **Lifecycle binding**: The instance's life is tied to the process — Ctrl+C destroys it.
- **Image templates**: Pick between curated image templates via `-i/--ami` (see `--list-templates`).
- **Detach mode**: Output instance metadata as JSON for scripting use.

## Image templates

Templates are baked into the package source (`vastinstance/image_templates.json`), not user-editable.
List them with:

```bash
vastinstance --list-templates
```

Currently:

- `nvidia-cuda` (default) — Nvidia CUDA base image with Jupyter, Tensorboard, and SSH.
- `linux-desktop-container` — Linux desktop in a container with Selkies/Guacamole/VNC, Jupyter, and SSH.
- `ubuntu-desktop-vm` — Ubuntu 22.04 desktop in a KVM VM with Selkies/Guacamole/VNC and SSH.

## Authentication

Set your vast.ai API key via one of:
1. `VAST_API_KEY` environment variable
2. `~/.config/vastai/vast_api_key` file (the file the `vastai` CLI maintains)
3. `--api-key` command line argument

Get an API key from: https://console.vast.ai/account/api-credit/

## SSH keys

`vastinstance` reuses your existing SSH private key in `~/.ssh/` (prefers `id_ed25519`).
For `--ssh` instances, vast.ai injects all public keys registered with your account, so make sure the
public key matching your `~/.ssh/id_ed25519` is registered:

```bash
vastai create ssh-key ~/.ssh/id_ed25519.pub
```