from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class NetworkInterfaceSpec:
    id: str
    ifname: str
    netns: str
    ip: str
    mac: str
    tofino_port: Optional[str]


@dataclass(frozen=True)
class EndpointSpec:
    id: str
    hostname: str
    network_interfaces: List[NetworkInterfaceSpec]


@dataclass(frozen=True)
class HostSpec:
    id: str
    hostname: str
    subnet: Optional[str]
    endpoints: List[str]


@dataclass(frozen=True)
class TopologyModel:
    raw: Dict[str, Any]
    hosts: Dict[str, HostSpec]
    endpoints: Dict[str, EndpointSpec]


def _require_dict(value: Any, field_name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a mapping/object")
    return value


def _require_list(value: Any, field_name: str) -> List[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return value


def load_topology(doc: Dict[str, Any]) -> TopologyModel:
    """Normalize the topology YAML/JSON into a small typed view."""

    hosts_raw = _require_list(doc.get("hosts", []), "hosts")
    endpoints_raw = _require_list(doc.get("endpoints", []), "endpoints")

    hosts: Dict[str, HostSpec] = {}
    for index, raw_host in enumerate(hosts_raw):
        host_obj = _require_dict(raw_host, f"hosts[{index}]")
        hostname = str(host_obj["hostname"])
        hosts[hostname] = HostSpec(
            id=str(host_obj["id"]),
            hostname=hostname,
            subnet=str(host_obj["subnet"]) if "subnet" in host_obj else None,
            endpoints=[str(endpoint_id) for endpoint_id in _require_list(host_obj.get("endpoints", []), f"hosts[{index}].endpoints")],
        )

    endpoints: Dict[str, EndpointSpec] = {}
    for index, raw_endpoint in enumerate(endpoints_raw):
        endpoint_obj = _require_dict(raw_endpoint, f"endpoints[{index}]")
        endpoint_id = str(endpoint_obj["id"])
        hostname = str(endpoint_obj["hostname"])
        nics_raw = _require_list(endpoint_obj.get("network_interfaces", []), f"endpoints[{index}].network_interfaces")
        network_interfaces = [
            NetworkInterfaceSpec(
                id=str(nic_obj["id"]),
                ifname=str(nic_obj["ifname"]),
                netns=str(nic_obj["netns"]),
                ip=str(nic_obj["ip"]),
                mac=str(nic_obj["mac"]),
                tofino_port=(str(nic_obj["tofino_port"]) if nic_obj.get("tofino_port") is not None else None),
            )
            for nic_obj in (_require_dict(raw_nic, f"endpoints[{index}].network_interfaces[]") for raw_nic in nics_raw)
        ]
        endpoints[endpoint_id] = EndpointSpec(
            id=endpoint_id,
            hostname=hostname,
            network_interfaces=network_interfaces,
        )

    return TopologyModel(raw=doc, hosts=hosts, endpoints=endpoints)
