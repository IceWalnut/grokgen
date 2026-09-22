# Allow inbound TCP to WSL for the grokgen project.
#
# Why this is needed: WSL mirrored networking puts the Hyper-V firewall in front
# of the WSL distro. Its DefaultInboundAction is Block and the only inbound allow
# rule is for loopback, so ports published inside WSL (sshd 2222, ComfyUI 8188)
# are reachable from the Windows host via 127.0.0.1 but not via the Tailscale
# address -- not from the host itself, and not from other tailnet devices.
#
# Scope: only these two TCP ports, only inbound, only the WSL VM.
# The Tailscale adapter is a Private network and the tailnet is the trust
# boundary, so this does not widen exposure beyond what was already in use.
#
# To undo:
#   Remove-NetFirewallHyperVRule -Name grokgen-wsl-ssh
#   Remove-NetFirewallHyperVRule -Name grokgen-wsl-comfyui

$ErrorActionPreference = 'Stop'

$vmCreatorId = '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'

$rules = @(
    @{ Name = 'grokgen-wsl-ssh';     Display = 'grokgen: WSL sshd (2222) inbound'; Port = 2222 },
    @{ Name = 'grokgen-wsl-comfyui'; Display = 'grokgen: ComfyUI (8188) inbound';  Port = 8188 }
)

foreach ($rule in $rules) {
    $existing = Get-NetFirewallHyperVRule -Name $rule.Name -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Output ("SKIP  " + $rule.Name + " already exists")
        continue
    }
    New-NetFirewallHyperVRule -Name $rule.Name -DisplayName $rule.Display `
        -Direction Inbound -VMCreatorId $vmCreatorId -Protocol TCP `
        -LocalPorts $rule.Port -Action Allow | Out-Null
    Write-Output ("ADDED " + $rule.Name + " tcp/" + $rule.Port)
}

Write-Output '--- current grokgen rules ---'
Get-NetFirewallHyperVRule | Where-Object { $_.Name -like 'grokgen-*' } |
    Select-Object Name, Direction, Action, Enabled | Format-Table -AutoSize
