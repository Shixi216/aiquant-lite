<#
.SYNOPSIS
    协助让外部朋友访问 Hermes OPC Web UI 的一键工具。
.DESCRIPTION
    提供两种方式：
    1. 【局域网/同一WiFi访问】获取本机局域网 IP 与防火墙放行指引；
    2. 【公网穿透访问（免公网IP）】利用 Cloudflare Tunnel 快速生成临时公网安全 HTTPS 访问链接。
#>
param(
    [int]$Port = 8767,
    [switch]$PublicTunnel,
    [switch]$AllowFirewall
)

$ErrorActionPreference = "Continue"
$projectRoot = Split-Path -Parent $PSScriptRoot

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "      Hermes OPC Web UI 外部访问助手                        " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# 1. 检测服务是否在运行
$webUiConn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $webUiConn) {
    Write-Host "[提示] 未检测到端口 $Port 正在监听。请确保 Web UI 已启动：" -ForegroundColor Yellow
    Write-Host "       python -m scripts.run_web_ui`n" -ForegroundColor Gray
} else {
    Write-Host "[状态] Web UI 服务正常监听于端口 $Port（监听地址：$($webUiConn.LocalAddress)）`n" -ForegroundColor Green
}

# 2. 获取局域网 IP 地址
$lanIps = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    Select-Object -ExpandProperty IPAddress

Write-Host "【方式 1：同一局域网 / WiFi 访问】" -ForegroundColor White
Write-Host "如果您的朋友连接在同一个 WiFi 或内网路由下，可直接发送以下链接：" -ForegroundColor Gray
foreach ($ip in $lanIps) {
    Write-Host "   -> http://${ip}:${Port}" -ForegroundColor Yellow
}

# 防火墙检查
$firewallRule = Get-NetFirewallRule -DisplayName "Hermes OPC Web UI" -ErrorAction SilentlyContinue
if (-not $firewallRule -and $AllowFirewall) {
    try {
        New-NetFirewallRule -DisplayName "Hermes OPC Web UI" -Direction Inbound -LocalPort $Port -Protocol TCP -Action Allow -ErrorAction Stop | Out-Null
        Write-Host "   [已配置] Windows 防火墙已成功放行端口 $Port" -ForegroundColor Green
    } catch {
        Write-Host "   [提示] 配置防火墙需要管理员权限：$_" -ForegroundColor Red
    }
} elseif (-not $firewallRule) {
    Write-Host "   [注] 若局域网无法打开，请以管理员身份运行 PowerShell 执行放行防火墙命令：" -ForegroundColor DarkGray
    Write-Host "        New-NetFirewallRule -DisplayName 'Hermes OPC Web UI' -Direction Inbound -LocalPort $Port -Protocol TCP -Action Allow" -ForegroundColor DarkGray
}

Write-Host "`n------------------------------------------------------------" -ForegroundColor DarkGray

# 3. 互联网远程穿透访问（Cloudflare Tunnel 免公网IP）
Write-Host "【方式 2：互联网远程访问（朋友在外部网络/异地）】" -ForegroundColor White
Write-Host "推荐使用 Cloudflare Quick Tunnel（完全免费、无需注册、无需公网IP，自动生成 HTTPS 链接）。" -ForegroundColor Gray

$toolsDir = Join-Path $projectRoot ".build-tools"
if (-not (Test-Path $toolsDir)) { New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null }
$cloudflaredPath = Join-Path $toolsDir "cloudflared.exe"

$hasCmd = [bool](Get-Command cloudflared -ErrorAction SilentlyContinue)
$hasFile = Test-Path -LiteralPath $cloudflaredPath
$hasCloudflared = $hasCmd -or $hasFile
$exeToRun = $cloudflaredPath
if ($hasCmd) {
    $exeToRun = "cloudflared"
}

if (-not $hasCloudflared) {
    Write-Host "`n正在检查 Cloudflare Tunnel 工具..." -ForegroundColor Cyan
    Write-Host "首次使用可通过此命令自动下载免安装 cloudflared.exe 工具：" -ForegroundColor Gray
    Write-Host "  Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile '$cloudflaredPath'" -ForegroundColor Yellow
}

if ($PublicTunnel) {
    if (-not (Test-Path $exeToRun) -and $exeToRun -ne "cloudflared") {
        Write-Host "正在下载 Cloudflare Tunnel 客户端..." -ForegroundColor Cyan
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile $cloudflaredPath -UseBasicParsing
            Write-Host "下载完成！" -ForegroundColor Green
        } catch {
            Write-Host "下载失败，请手动下载 cloudflared.exe 并放入 .build-tools 目录: $_" -ForegroundColor Red
            return
        }
    }

    Write-Host "`n正在启动公网安全通道，请稍候..." -ForegroundColor Cyan
    Write-Host "启动后控制台中会输出包含 trycloudflare.com 的网址，直接复制发给朋友即可访问！" -ForegroundColor Green
    Write-Host "（按 Ctrl+C 可停止分享通道）`n" -ForegroundColor DarkGray

    & $exeToRun tunnel --url "http://127.0.0.1:$Port"
} else {
    Write-Host "`n启动一键公网分享通道命令：" -ForegroundColor White
    Write-Host "   powershell -ExecutionPolicy Bypass -File scripts/share_web_ui.ps1 -PublicTunnel" -ForegroundColor Yellow
}

Write-Host "============================================================" -ForegroundColor Cyan
