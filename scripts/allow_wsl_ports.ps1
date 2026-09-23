# Allow inbound TCP to the WSL distro for the grokgen project, from the tailnet only.
#
# ── Why this is needed ────────────────────────────────────────────────────────
# WSL mirrored networking puts a Hyper-V firewall in front of the WSL distro.
# Its DefaultInboundAction is Block, so ports published inside WSL (sshd 2222,
# ComfyUI 8188, the gateway 7869) can be unreachable from other tailnet devices
# even though they work from the Windows host over 127.0.0.1.
#
# ── What actually limits the exposure (measured 2026-09-23) ───────────────────
# ⚠️ An earlier version of this comment credited the Tailscale adapter's
# "Private" classification at THIS layer. That was the wrong layer. Measured:
#
#   Windows host firewall  ← this is the layer that actually blocks the LAN.
#       Ethernet (home LAN 192.168.71.x) is classified Public; inbound is
#       denied by default and no rule opens 2222/7869/8188 on Public.
#       The Tailscale adapter is Private, and Tailscale's own "Tailscale-In"
#       host rule (scoped to Domain+Private) lets tailnet traffic through.
#
#   Hyper-V firewall (this file)  ← currently WIDE OPEN, not a port filter.
#       Tailscale also installs a Hyper-V rule named "Tailscale-In" with
#       VMCreatorId=Any, any port, any protocol, any source. That is why 7869
#       was reachable before this script ever mentioned it.
#
# So adding 7869 here does NOT widen exposure -- this layer is already open.
# Its value is durability: Tailscale's blanket rule is not ours to control, and
# the "all WSL ports unreachable" incident of 2026-09-22 was never root-caused.
#
# ── Why the rules are scoped to the tailnet ranges ────────────────────────────
# The protection above rests entirely on the Ethernet adapter staying classified
# as Public. That classification is one click away from changing (Windows
# occasionally prompts "make this PC discoverable?"), and Tailscale-In covers
# Private. If that happens, every device on the home Wi-Fi can reach the gateway
# -- which has no authentication at all (contract section 1: the tailnet IS the
# trust boundary). Scoping by source address keeps one barrier at this layer
# even if the adapter is reclassified.
#
# Both address families are listed: Tailscale assigns each device an IPv4 in
# 100.64.0.0/10 and an IPv6 in fd7a:115c:a1e0::/48. Listing only IPv4 would
# silently drop connections that happen to go over IPv6.
#
# Scope: only these three TCP ports, only inbound, only the WSL VM,
# only from tailnet source addresses.
#
# To undo (per rule):
#   Set-NetFirewallHyperVRule -Name grokgen-wsl-gateway -RemoteAddresses Any
#   Remove-NetFirewallHyperVRule -Name grokgen-wsl-gateway

$ErrorActionPreference = 'Stop'

$vmCreatorId = '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'

# Tailscale's own address ranges. See the comment above for why both are needed.
$tailnet = @('100.64.0.0/10', 'fd7a:115c:a1e0::/48')

$rules = @(
    @{ Name = 'grokgen-wsl-ssh';     Display = 'grokgen: WSL sshd (2222) inbound';   Port = 2222 },
    @{ Name = 'grokgen-wsl-comfyui'; Display = 'grokgen: ComfyUI (8188) inbound';    Port = 8188 },
    @{ Name = 'grokgen-wsl-gateway'; Display = 'grokgen: gateway (7869) inbound';    Port = 7869 }
)

foreach ($rule in $rules) {
    $existing = Get-NetFirewallHyperVRule -Name $rule.Name -ErrorAction SilentlyContinue

    if ($existing) {
        # ⚠️ Do not just SKIP here. The previous version of this script skipped
        # every existing rule, which meant re-running it could never tighten a
        # rule that was created before the tailnet scoping was added -- the
        # script would report success while leaving RemoteAddresses = Any.
        $current = @($existing.RemoteAddresses) -join ','
        $wanted  = $tailnet -join ','
        if ($current -eq $wanted) {
            Write-Output ("OK    " + $rule.Name + " already scoped to the tailnet")
        } else {
            Set-NetFirewallHyperVRule -Name $rule.Name -RemoteAddresses $tailnet | Out-Null
            Write-Output ("FIXED " + $rule.Name + " remote addresses: " + $current + " -> " + $wanted)
        }
        continue
    }

    New-NetFirewallHyperVRule -Name $rule.Name -DisplayName $rule.Display `
        -Direction Inbound -VMCreatorId $vmCreatorId -Protocol TCP `
        -LocalPorts $rule.Port -RemoteAddresses $tailnet -Action Allow | Out-Null
    Write-Output ("ADDED " + $rule.Name + " tcp/" + $rule.Port + " from tailnet only")
}

Write-Output '--- current grokgen rules ---'
Get-NetFirewallHyperVRule | Where-Object { $_.Name -like 'grokgen-*' } |
    Select-Object Name, Direction, Action, LocalPorts, RemoteAddresses, Enabled |
    Format-Table -AutoSize
