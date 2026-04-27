# WireGuard Tunnel — Troubleshooting Checklist

## Prevention

**Server side** — config file `/etc/wireguard/wg0.conf` now has `AllowedIPs = 0.0.0.0/0` permanently. Survives reboots.

**Monitor self-heals** — `/home/ubuntu/wg_monitor.sh` runs every 5 min and auto-resets `AllowedIPs` if it drifts.

**Router side** — iptables rules are wiped on router reboot. No way to auto-fix without JFFS or Merlin firmware. Must be re-added manually. See below.

---

## When you get a Telegram alert

### Alert: "WG self-heal: AllowedIPs was X, reset to 0.0.0.0/0"
Already fixed automatically. No action needed.

### Alert: "WG tunnel forwarding DOWN. Router iptables were flushed"
**The router rebooted.** SSH in from your home LAN and run:

```bash
iptables -I FORWARD 6 -i wgc5 -o eth0 -j ACCEPT
iptables -t nat -A POSTROUTING -s 10.99.0.0/24 -o eth0 -j MASQUERADE
```

Monitor will send "restored" confirmation within 5 min.

### Alert: "WG tunnel forwarding restored"
All good.

---

## Manual diagnosis checklist

Run on the **server** to check what's wrong:

```bash
# 1. Tunnel handshake active?
sudo wg show
# Look for: "latest handshake: X seconds ago" (should be < 3 min)
# Look for: "allowed ips: 0.0.0.0/0" (NOT 10.99.0.2/32)

# 2. Traffic exits through home IP?
curl -s --interface wg0 ifconfig.me
# Should return: 151.251.106.23 (your home IP)

# 3. Docker container exits through home IP?
docker run --rm --network wg_net curlimages/curl curl -s ifconfig.me

# 4. Server's own IP (for comparison)
curl -s ifconfig.me
# Returns: 130.61.180.78 (Oracle)
```

### If handshake is stale (> 3 min)
- Router's WireGuard client might be disconnected
- Check router: **VPN > VPN Fusion > WireGuard profile**
- Toggle it off/on

### If handshake is fresh but no home IP returned
- Router iptables were flushed (see alert fix above)

### If `AllowedIPs` shows `10.99.0.2/32`
- Self-heal should fix within 5 min, or run manually:
```bash
sudo wg set wg0 peer d9nF0hgrRCFggr7oO67X5Hd6mgOQOXnu4AVQGYE9rWg= allowed-ips 0.0.0.0/0
```

---

## Server-side restart (full reset)

If nothing works:

```bash
sudo systemctl restart wg-quick@wg0
sudo wg show
```
