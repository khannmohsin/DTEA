from __future__ import annotations

from copy import deepcopy
from typing import Any


NETWORK_PROFILES: dict[str, dict[str, Any]] = {
    "fog-lan": {
        "label": "Fog LAN",
        "delay_ms": 2,
        "jitter_ms": 1,
        "loss_percent": 0.0,
    },
    "fog-lite": {
        "label": "Fog Lite",
        "delay_ms": 5,
        "jitter_ms": 2,
        "loss_percent": 0.1,
    },
    "edge-standard": {
        "label": "Edge Standard",
        "delay_ms": 12,
        "jitter_ms": 4,
        "loss_percent": 0.2,
    },
    "edge-industrial": {
        "label": "Edge Industrial",
        "delay_ms": 15,
        "jitter_ms": 5,
        "loss_percent": 0.3,
    },
    "endpoint-light": {
        "label": "Endpoint Light",
        "delay_ms": 25,
        "jitter_ms": 10,
        "loss_percent": 0.6,
    },
    "endpoint-standard": {
        "label": "Endpoint Standard",
        "delay_ms": 20,
        "jitter_ms": 8,
        "loss_percent": 0.4,
    },
    "endpoint-industrial": {
        "label": "Endpoint Industrial",
        "delay_ms": 18,
        "jitter_ms": 6,
        "loss_percent": 0.3,
    },
}


DEVICE_PRESETS: dict[str, list[dict[str, Any]]] = {
    "fog": [
        {
            "id": "jetson-orin-nano-8gb",
            "label": "Jetson Orin Nano 8GB",
            "tier": "fog",
            "runnable": True,
            "ietf_class_badge": "Above C2",
            "official_specs": {
                "cpu": "6-core Arm Cortex-A78AE",
                "ram": "8GB LPDDR5",
                "storage": "microSD plus M.2/NVMe capable",
                "networking": "Gigabit Ethernet",
                "power": "7W-15W class",
                "runtime_mode": "Chain-backed validator-capable node",
                "ietf_class_relation": "Above C2",
                "notes": "GPU/NPU/TOPS are informational only and are not directly emulated by the container runtime.",
            },
            "simulation_profile": {
                "vcpu": 6.0,
                "memory_mb": 8192,
                "storage_gb": 64,
                "network_profile": "fog-lan",
                "chain_backed": True,
                "runtime_backend": "container",
            },
        },
        {
            "id": "jetson-xavier-nx-8gb",
            "label": "Jetson Xavier NX 8GB",
            "tier": "fog",
            "runnable": True,
            "ietf_class_badge": "Above C2",
            "official_specs": {
                "cpu": "6-core Carmel Arm",
                "ram": "8GB LPDDR4x",
                "storage": "microSD plus NVMe capable carrier options",
                "networking": "Gigabit Ethernet",
                "power": "10W-20W class",
                "runtime_mode": "Chain-backed validator-capable node",
                "ietf_class_relation": "Above C2",
                "notes": "Accelerator throughput is informational only and is not directly emulated by the container runtime.",
            },
            "simulation_profile": {
                "vcpu": 4.0,
                "memory_mb": 8192,
                "storage_gb": 64,
                "network_profile": "fog-lan",
                "chain_backed": True,
                "runtime_backend": "container",
            },
        },
        {
            "id": "jetson-nano-4gb",
            "label": "Jetson Nano 4GB",
            "tier": "fog",
            "runnable": True,
            "ietf_class_badge": "Above C2",
            "official_specs": {
                "cpu": "Quad Cortex-A57",
                "ram": "4GB LPDDR4",
                "storage": "microSD boot media",
                "networking": "Gigabit Ethernet",
                "power": "5W-10W class",
                "runtime_mode": "Chain-backed validator-capable node",
                "ietf_class_relation": "Above C2",
                "notes": "GPU/TOPS are informational only and are not directly emulated by the container runtime.",
            },
            "simulation_profile": {
                "vcpu": 2.0,
                "memory_mb": 4096,
                "storage_gb": 32,
                "network_profile": "fog-lan",
                "chain_backed": True,
                "runtime_backend": "container",
            },
        },
        {
            "id": "raspberry-pi-5-8gb-fog",
            "label": "Raspberry Pi 5 8GB",
            "tier": "fog",
            "runnable": True,
            "ietf_class_badge": "Above C2",
            "official_specs": {
                "cpu": "Quad Cortex-A76 @ 2.4GHz",
                "ram": "8GB LPDDR4X",
                "storage": "microSD plus PCIe x1 SSD capable",
                "networking": "Gigabit Ethernet, Wi-Fi 5",
                "power": "5V/5A SBC class",
                "runtime_mode": "Chain-backed validator-capable node",
                "ietf_class_relation": "Above C2",
                "notes": "PCIe and GPU details are informational only and are not directly emulated by the container runtime.",
            },
            "simulation_profile": {
                "vcpu": 4.0,
                "memory_mb": 8192,
                "storage_gb": 32,
                "network_profile": "fog-lite",
                "chain_backed": True,
                "runtime_backend": "container",
            },
        },
    ],
    "edge": [
        {
            "id": "raspberry-pi-5-8gb-edge",
            "label": "Raspberry Pi 5 8GB",
            "tier": "edge",
            "runnable": True,
            "ietf_class_badge": "Above C2",
            "official_specs": {
                "cpu": "Quad Cortex-A76 @ 2.4GHz",
                "ram": "8GB LPDDR4X",
                "storage": "microSD plus PCIe x1 SSD capable",
                "networking": "Gigabit Ethernet, Wi-Fi 5",
                "power": "5V/5A SBC class",
                "runtime_mode": "Chain-backed edge node",
                "ietf_class_relation": "Above C2",
                "notes": "GPU and PCIe details are informational only and are not directly emulated by the container runtime.",
            },
            "simulation_profile": {
                "vcpu": 4.0,
                "memory_mb": 8192,
                "storage_gb": 32,
                "network_profile": "edge-standard",
                "chain_backed": True,
                "runtime_backend": "container",
            },
        },
        {
            "id": "raspberry-pi-4-4gb",
            "label": "Raspberry Pi 4 4GB",
            "tier": "edge",
            "runnable": True,
            "ietf_class_badge": "Above C2",
            "official_specs": {
                "cpu": "Quad Cortex-A72 @ 1.8GHz",
                "ram": "4GB LPDDR4",
                "storage": "microSD boot media",
                "networking": "Gigabit Ethernet, Wi-Fi 5",
                "power": "USB-C SBC class",
                "runtime_mode": "Chain-backed edge node",
                "ietf_class_relation": "Above C2",
                "notes": "Wireless and video features are informational only and are not directly emulated by the container runtime.",
            },
            "simulation_profile": {
                "vcpu": 2.0,
                "memory_mb": 4096,
                "storage_gb": 32,
                "network_profile": "edge-standard",
                "chain_backed": True,
                "runtime_backend": "container",
            },
        },
        {
            "id": "compute-module-4-4gb",
            "label": "Compute Module 4 4GB",
            "tier": "edge",
            "runnable": True,
            "ietf_class_badge": "Above C2",
            "official_specs": {
                "cpu": "Quad Cortex-A72 @ 1.5GHz",
                "ram": "4GB LPDDR4",
                "storage": "Optional 8/16/32GB eMMC",
                "networking": "Carrier-board dependent, optional wireless",
                "power": "Industrial SOM class",
                "runtime_mode": "Chain-backed edge node",
                "ietf_class_relation": "Above C2",
                "notes": "Carrier-board peripherals are informational only and are not directly emulated by the container runtime.",
            },
            "simulation_profile": {
                "vcpu": 2.0,
                "memory_mb": 4096,
                "storage_gb": 16,
                "network_profile": "edge-industrial",
                "chain_backed": True,
                "runtime_backend": "container",
            },
        },
        {
            "id": "compute-module-5-4gb",
            "label": "Compute Module 5 4GB",
            "tier": "edge",
            "runnable": True,
            "ietf_class_badge": "Above C2",
            "official_specs": {
                "cpu": "Quad Cortex-A76 @ 2.4GHz",
                "ram": "4GB LPDDR4X",
                "storage": "Optional 16/32/64GB eMMC",
                "networking": "Carrier-board dependent, optional wireless",
                "power": "Industrial SOM class",
                "runtime_mode": "Chain-backed edge node",
                "ietf_class_relation": "Above C2",
                "notes": "Carrier-board peripherals are informational only and are not directly emulated by the container runtime.",
            },
            "simulation_profile": {
                "vcpu": 3.0,
                "memory_mb": 4096,
                "storage_gb": 16,
                "network_profile": "edge-industrial",
                "chain_backed": True,
                "runtime_backend": "container",
            },
        },
    ],
    "endpoint": [
        {
            "id": "raspberry-pi-zero-2-w",
            "label": "Raspberry Pi Zero 2 W",
            "tier": "endpoint",
            "runnable": True,
            "ietf_class_badge": "Above C2",
            "official_specs": {
                "cpu": "Quad Cortex-A53 @ 1GHz",
                "ram": "512MB SDRAM",
                "storage": "microSD boot media",
                "networking": "2.4GHz Wi-Fi, Bluetooth 4.2",
                "power": "Low-power SBC class",
                "runtime_mode": "API-only endpoint node",
                "ietf_class_relation": "Above C2",
                "notes": "This is Linux-capable but still much larger than RFC 7228 constrained classes.",
            },
            "simulation_profile": {
                "vcpu": 0.5,
                "memory_mb": 512,
                "storage_gb": 8,
                "network_profile": "endpoint-light",
                "chain_backed": False,
                "runtime_backend": "container",
            },
        },
        {
            "id": "raspberry-pi-4-2gb",
            "label": "Raspberry Pi 4 2GB",
            "tier": "endpoint",
            "runnable": True,
            "ietf_class_badge": "Above C2",
            "official_specs": {
                "cpu": "Quad Cortex-A72 @ 1.8GHz",
                "ram": "2GB LPDDR4",
                "storage": "microSD boot media",
                "networking": "Gigabit Ethernet, Wi-Fi 5",
                "power": "USB-C SBC class",
                "runtime_mode": "API-only endpoint node",
                "ietf_class_relation": "Above C2",
                "notes": "This is Linux-capable and substantially larger than RFC 7228 constrained classes.",
            },
            "simulation_profile": {
                "vcpu": 1.0,
                "memory_mb": 2048,
                "storage_gb": 16,
                "network_profile": "endpoint-standard",
                "chain_backed": False,
                "runtime_backend": "container",
            },
        },
        {
            "id": "compute-module-4-2gb",
            "label": "Compute Module 4 2GB",
            "tier": "endpoint",
            "runnable": True,
            "ietf_class_badge": "Above C2",
            "official_specs": {
                "cpu": "Quad Cortex-A72 @ 1.5GHz",
                "ram": "2GB LPDDR4",
                "storage": "Optional eMMC depending on variant",
                "networking": "Carrier-board dependent, optional wireless",
                "power": "Industrial SOM class",
                "runtime_mode": "API-only endpoint node",
                "ietf_class_relation": "Above C2",
                "notes": "This is Linux-capable and substantially larger than RFC 7228 constrained classes.",
            },
            "simulation_profile": {
                "vcpu": 1.0,
                "memory_mb": 2048,
                "storage_gb": 16,
                "network_profile": "endpoint-industrial",
                "chain_backed": False,
                "runtime_backend": "container",
            },
        },
        {
            "id": "compute-module-5-2gb",
            "label": "Compute Module 5 2GB",
            "tier": "endpoint",
            "runnable": True,
            "ietf_class_badge": "Above C2",
            "official_specs": {
                "cpu": "Quad Cortex-A76 @ 2.4GHz",
                "ram": "2GB LPDDR4X",
                "storage": "Optional 16/32/64GB eMMC",
                "networking": "Carrier-board dependent, optional wireless",
                "power": "Industrial SOM class",
                "runtime_mode": "API-only endpoint node",
                "ietf_class_relation": "Above C2",
                "notes": "This is Linux-capable and substantially larger than RFC 7228 constrained classes.",
            },
            "simulation_profile": {
                "vcpu": 2.0,
                "memory_mb": 2048,
                "storage_gb": 16,
                "network_profile": "endpoint-industrial",
                "chain_backed": False,
                "runtime_backend": "container",
            },
        },
    ],
}


IETF_ENDPOINT_REFERENCE = {
    "classes": [
        {"class": "C0", "ram": "<< 10 KiB", "code": "<< 100 KiB"},
        {"class": "C1", "ram": "~ 10 KiB", "code": "~ 100 KiB"},
        {"class": "C2", "ram": "~ 50 KiB", "code": "~ 250 KiB"},
    ],
    "devices": [
        {
            "label": "Raspberry Pi Pico W",
            "cpu": "RP2040 dual-core Arm Cortex-M0+",
            "ram": "264 kB SRAM",
            "storage": "2 MB flash",
            "networking": "2.4GHz Wi-Fi",
            "runnable": False,
            "ietf_class_badge": "Reference-only",
            "note": "Reference-only, not runnable with the current BlockCap endpoint stack.",
        },
        {
            "label": "Raspberry Pi Pico 2 W",
            "cpu": "RP2350 dual-core Cortex-M33 or Hazard3",
            "ram": "520 kB SRAM",
            "storage": "4 MB flash",
            "networking": "2.4GHz Wi-Fi",
            "runnable": False,
            "ietf_class_badge": "Reference-only",
            "note": "Reference-only, not runnable with the current BlockCap endpoint stack.",
        },
    ],
    "note": "Runnable Linux endpoint presets in this UI are all above RFC 7228 class C2.",
}


def _preset_lookup(tier: str) -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in DEVICE_PRESETS.get(str(tier), [])}


def default_device_id(tier: str) -> str:
    rows = DEVICE_PRESETS.get(str(tier), [])
    if not rows:
        raise ValueError(f"Unsupported device tier: {tier}")
    return str(rows[0]["id"])


def resolve_device(device_id: str | None, tier: str) -> dict[str, Any]:
    preset_id = str(device_id or default_device_id(tier))
    lookup = _preset_lookup(tier)
    if preset_id not in lookup:
        raise ValueError(f"Unsupported {tier} device preset: {preset_id}")
    row = deepcopy(lookup[preset_id])
    profile = dict(row.get("simulation_profile") or {})
    network_key = str(profile.get("network_profile") or "")
    if network_key and network_key in NETWORK_PROFILES:
        profile["network"] = deepcopy(NETWORK_PROFILES[network_key])
    row["simulation_profile"] = profile
    return row


def normalize_device_ids(*, tier: str, count: int, selected_ids: list[str] | None) -> list[str]:
    chosen = [str(item).strip() for item in (selected_ids or []) if str(item).strip()]
    if not chosen:
        return [default_device_id(tier)] * max(0, int(count))
    if len(chosen) != int(count):
        raise ValueError(f"{tier} device count does not match the number of selected presets")
    for preset_id in chosen:
        resolve_device(preset_id, tier)
    return chosen


def device_catalog_payload() -> dict[str, Any]:
    return {
        "tiers": deepcopy(DEVICE_PRESETS),
        "network_profiles": deepcopy(NETWORK_PROFILES),
        "endpoint_reference": deepcopy(IETF_ENDPOINT_REFERENCE),
        "defaults": {
            tier: default_device_id(tier)
            for tier in DEVICE_PRESETS
        },
    }

