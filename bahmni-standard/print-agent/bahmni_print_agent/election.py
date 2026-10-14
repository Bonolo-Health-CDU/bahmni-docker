from __future__ import annotations

import json
import logging
import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .config import ElectionConfig

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PeerState:
    hostname: str
    priority: int
    last_seen: float
    role: str
    address: str = ""

    def to_dict(self, now: float) -> dict:
        return {
            "hostname": self.hostname,
            "priority": self.priority,
            "role": self.role,
            "address": self.address,
            "last_seen_seconds_ago": round(max(now - self.last_seen, 0), 1),
        }


class PeerElection:
    ROLE_ACTIVE = "active"
    ROLE_STANDBY = "standby"
    MESSAGE_VERSION = 1

    def __init__(
        self,
        agent_name: str,
        config: ElectionConfig,
        clock: Callable[[], float] | None = None,
    ):
        self.agent_name = agent_name
        self.config = config
        self.identity = (
            config.hostname
            or socket.gethostname()
            or agent_name
            or "CDU-PRINT-AGENT"
        )
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._peers: dict[str, PeerState] = {}
        self._role = self.ROLE_STANDBY
        self._force_active = config.force_active
        self._listener_thread: threading.Thread | None = None
        self._broadcast_thread: threading.Thread | None = None
        self._listener_socket: socket.socket | None = None
        self._broadcast_socket: socket.socket | None = None

    def start(self):
        if self._listener_thread and self._listener_thread.is_alive():
            return
        self._stop_event.clear()
        self._update_role()
        self._listener_thread = threading.Thread(
            target=self._listen_loop,
            name="print-agent-election-listener",
            daemon=True,
        )
        self._broadcast_thread = threading.Thread(
            target=self._broadcast_loop,
            name="print-agent-election-broadcaster",
            daemon=True,
        )
        self._listener_thread.start()
        self._broadcast_thread.start()

    def stop(self):
        self._stop_event.set()
        for election_socket in (self._listener_socket, self._broadcast_socket):
            if election_socket:
                try:
                    election_socket.close()
                except OSError:
                    pass
        for thread in (self._listener_thread, self._broadcast_thread):
            if thread and thread.is_alive():
                thread.join(timeout=1.0)

    @property
    def role(self) -> str:
        self._update_role()
        with self._lock:
            return self._role

    @property
    def is_active(self) -> bool:
        return self.role == self.ROLE_ACTIVE

    def set_force_active(self, enabled: bool = True):
        with self._lock:
            self._force_active = bool(enabled)
        self._update_role()

    def record_peer(
        self,
        hostname: str,
        priority: int,
        role: str = ROLE_STANDBY,
        address: str = "",
    ):
        hostname = str(hostname or "").strip()
        if not hostname or hostname == self.identity:
            return
        try:
            priority = int(priority)
        except (TypeError, ValueError):
            priority = self.config.priority
        with self._lock:
            self._peers[hostname] = PeerState(
                hostname=hostname,
                priority=priority,
                last_seen=self._clock(),
                role=str(role or "").lower() or "unknown",
                address=address,
            )
        self._update_role()

    def snapshot(self) -> dict:
        self._update_role()
        with self._lock:
            now = self._clock()
            peers = self._visible_peers_locked(now)
            active_hostname = self._active_hostname_locked(now, peers)
            return {
                "enabled": True,
                "hostname": self.identity,
                "priority": self.config.priority,
                "role": self._role,
                "force_active": self._force_active,
                "active_hostname": active_hostname,
                "peers": [peer.to_dict(now) for peer in peers],
            }

    def _listen_loop(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._listener_socket = listener
        try:
            self._set_reuse_address(listener)
            listener.settimeout(0.5)
            listener.bind((self.config.bind_host, self.config.port))
            while not self._stop_event.is_set():
                try:
                    data, address = listener.recvfrom(4096)
                except socket.timeout:
                    self._update_role()
                    continue
                except OSError:
                    if self._stop_event.is_set():
                        return
                    _logger.exception("Peer election listener failed")
                    time.sleep(1)
                    continue
                self._record_peer_packet(data, address)
        except OSError:
            _logger.exception(
                "Peer election could not listen on UDP port %s",
                self.config.port,
            )
        finally:
            try:
                listener.close()
            except OSError:
                pass

    def _broadcast_loop(self):
        broadcaster = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._broadcast_socket = broadcaster
        try:
            broadcaster.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            while not self._stop_event.is_set():
                self._update_role()
                try:
                    broadcaster.sendto(
                        self._broadcast_payload(),
                        (self.config.broadcast_address, self.config.port),
                    )
                except OSError:
                    if not self._stop_event.is_set():
                        _logger.exception("Peer election broadcast failed")
                self._stop_event.wait(self.config.broadcast_interval_seconds)
        finally:
            try:
                broadcaster.close()
            except OSError:
                pass

    def _record_peer_packet(self, data: bytes, address):
        try:
            message = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _logger.debug("Ignoring malformed peer election packet")
            return
        if message.get("version") not in (None, self.MESSAGE_VERSION):
            return
        self.record_peer(
            hostname=message.get("hostname"),
            priority=message.get("priority"),
            role=message.get("current_role") or message.get("role"),
            address=address[0] if address else "",
        )

    def _broadcast_payload(self) -> bytes:
        with self._lock:
            payload = {
                "version": self.MESSAGE_VERSION,
                "hostname": self.identity,
                "priority": self.config.priority,
                "timestamp": time.time(),
                "current_role": self._role,
            }
        return json.dumps(payload, sort_keys=True).encode("utf-8")

    def _update_role(self):
        with self._lock:
            now = self._clock()
            peers = self._visible_peers_locked(now)
            active_hostname = self._active_hostname_locked(now, peers)
            next_role = (
                self.ROLE_ACTIVE
                if active_hostname == self.identity
                else self.ROLE_STANDBY
            )
            if next_role != self._role:
                _logger.info(
                    "Print agent %s changed role from %s to %s",
                    self.identity,
                    self._role,
                    next_role,
                )
                self._role = next_role

    def _active_hostname_locked(
        self,
        now: float,
        peers: list[PeerState] | None = None,
    ) -> str:
        if self._force_active:
            return self.identity
        visible_peers = peers if peers is not None else self._visible_peers_locked(now)
        candidates = list(visible_peers)
        candidates.append(
            PeerState(
                hostname=self.identity,
                priority=self.config.priority,
                last_seen=now,
                role=self._role,
                address="self",
            )
        )
        return min(
            candidates,
            key=lambda peer: (peer.priority, peer.hostname.casefold()),
        ).hostname

    def _visible_peers_locked(self, now: float) -> list[PeerState]:
        expired = [
            hostname
            for hostname, peer in self._peers.items()
            if now - peer.last_seen > self.config.peer_timeout_seconds
        ]
        for hostname in expired:
            self._peers.pop(hostname, None)
        return list(self._peers.values())

    def _set_reuse_address(self, udp_socket: socket.socket):
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        reuse_port = getattr(socket, "SO_REUSEPORT", None)
        if reuse_port is not None:
            try:
                udp_socket.setsockopt(socket.SOL_SOCKET, reuse_port, 1)
            except OSError:
                pass
