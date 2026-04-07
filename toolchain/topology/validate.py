from __future__ import annotations

from typing import Set

from .model import TopologyModel


def validate_topology(topo: TopologyModel) -> None:
    """Run lightweight structural checks on the topology model."""

    seen_ifnames: Set[str] = set()
    seen_ips: Set[str] = set()
    seen_macs: Set[str] = set()

    for hostname, host in topo.hosts.items():
        for endpoint_id in host.endpoints:
            if endpoint_id not in topo.endpoints:
                raise ValueError(f"host {hostname!r} references unknown endpoint {endpoint_id!r}")

    for endpoint_id, endpoint in topo.endpoints.items():
        if endpoint.hostname not in topo.hosts:
            raise ValueError(f"endpoint {endpoint_id!r} references unknown host {endpoint.hostname!r}")

        host = topo.hosts[endpoint.hostname]
        if endpoint_id not in host.endpoints:
            raise ValueError(
                f"endpoint {endpoint_id!r} belongs to host {endpoint.hostname!r} "
                "but is not listed in that host's endpoint inventory"
            )

        for nic in endpoint.network_interfaces:
            if nic.ifname in seen_ifnames:
                raise ValueError(f"duplicate interface name detected: {nic.ifname}")
            seen_ifnames.add(nic.ifname)

            ip_no_mask = nic.ip.split("/")[0]
            if ip_no_mask in seen_ips:
                raise ValueError(f"duplicate IP address detected: {ip_no_mask}")
            seen_ips.add(ip_no_mask)

            if nic.mac in seen_macs:
                raise ValueError(f"duplicate MAC address detected: {nic.mac}")
            seen_macs.add(nic.mac)
