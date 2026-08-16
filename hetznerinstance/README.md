# ec2instance-hetzner

Launch new Hetzner Cloud servers easily and quickly with one command. Inspired by [ec2instance](https://github.com/personalcomputer/ec2instance).

## Quick Start

```bash
# Set your Hetzner Cloud API token
export HCLOUD_TOKEN="your-api-token"

# Launch a server (defaults: cx23, ubuntu (latest), fsn1)
hetznerinstance

# Launch with custom type, image, and location
hetznerinstance -t cax21 -i ubuntu -l hel1

# Detach mode - output JSON metadata and don't open shell
hetznerinstance --detach

# Custom user-data script
hetznerinstance -f /path/to/my-script.sh
```

## Features

- **One-command launch**: Just run `hetznerinstance` and get a shell on a fresh server.
- **Lifecycle binding**: The server's life is tied to the process — Ctrl+C terminates it.
- **Auto-provisioning**: SSH keys, firewall rules are created automatically on first run.
- **Customizable**: Choose server type, image, location, and user-data script.
- **Detach mode**: Output server metadata as JSON for scripting use.

## Configuration

Data is stored in `~/.config/hetznerinstance/`:
- SSH keys
- User data scripts

Use `hetznerinstance --show-data-path` to see the exact path.

## Authentication

Set your Hetzner Cloud API token via one of:
1. `HCLOUD_TOKEN` environment variable
2. `HETZNER_TOKEN` environment variable
3. `--token` command line argument

Get an API token from: https://console.hetzner.cloud/projects (Settings > API Tokens)
