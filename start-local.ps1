<#
.SYNOPSIS
    工程监理质量智能评估系统 —— 本机一键启动（数据层用 Docker，应用跑本机进程）

.DESCRIPTION
    适用于「本机已有 Python/Node 依赖，但希望一条命令拉起整套系统」的场景，
    也适用于 Docker Hub 不可达、无法构建应用镜像的环境。

    自动完成：启动数据层容器 → 检查依赖 → （可选）写入示例数据 →
    启动后端与前端 → 等待就绪 → 打印地址；按 Ctrl+C 一次性停止全部。

.PARAMETER Offline
    使用 Fake 大模型（不调用真实模型），无 API Key 也能跑通全流程。

.PARAMETER Seed
    启动前写入示例规范数据（4 份规范 / 94 个分块）。
    seed_data.py 是幂等的，已存在则跳过，可放心重复使用。

.PARAMETER ApiOnly
    只启动后端，不启动前端（用已有前端或只调接口）。

.EXAMPLE
    .\start-local.ps1                 # 一键启动（读取 backend\.env 的真实模型配置）
    .\start-local.ps1 -Offline        # 离线演示（Fake 模型）
    .\start-local.ps1 -Seed           # 顺带写入示例数据
    .\start-local.ps1 -ApiOnly        # 只起后端
#>
[CmdletBinding()]
param(
    [switch]$Offline,
    [switch]$Seed,
    [switch]$ApiOnly,
    [int]$ApiPort = 8000,
    [int]$WebPort = 5173
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Backend = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$Deploy = Join-Path $Root 'deploy'
$VarDir = Join-Path $Backend 'var'
$LogDir = Join-Path $VarDir 'logs'

function Write-Step([string]$Text) { Write-Host "`n=== $Text ===" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "  [OK] $Text" -ForegroundColor Green }
function Write-Warn2([string]$Text) { Write-Host "  [!]  $Text" -ForegroundColor Yellow }
function Write-Err([string]$Text) { Write-Host "  [X] $Text" -ForegroundColor Red }

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# --------------------------------------------------------------------------- #
# 定位 Python 与 Node
# --------------------------------------------------------------------------- #
function Resolve-Python {
    $candidates = @(
        'D:\Miniconda3\envs\test001\python.exe',
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
    )
    foreach ($c in $candidates) { if ($c -and (Test-Path $c)) { return $c } }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Resolve-Node {
    $candidates = @('C:\Program Files\nodejs\node.exe')
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    $cmd = Get-Command node -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

Write-Step '环境检查'
$python = Resolve-Python
if (-not $python) { Write-Err '未找到 Python，请安装或把 python 加入 PATH'; exit 1 }
Write-Ok "Python: $python  ($(& $python --version 2>&1))"

$node = Resolve-Node
if (-not $node) {
    if ($ApiOnly) {
        Write-Warn2 '未找到 Node，已跳过前端（-ApiOnly）'
    } else {
        Write-Err '未找到 Node，前端无法启动；可加 -ApiOnly 只起后端'
        exit 1
    }
} else {
    Write-Ok "Node:   $node  ($(& $node --version))"
}

$viteBin = Join-Path $Frontend 'node_modules\vite\bin\vite.js'
if (-not $ApiOnly -and -not (Test-Path $viteBin)) {
    Write-Warn2 '前端依赖未安装，先执行 npm install（约 1-2 分钟）'
    Push-Location $Frontend
    try {
        $env:npm_config_cache = Join-Path $Frontend '.npm-cache'
        & npm install --registry=https://registry.npmmirror.com --no-audit --no-fund --ignore-scripts
    } finally { Pop-Location }
    if (-not (Test-Path $viteBin)) { Write-Err '前端依赖安装失败，请手动执行 npm install'; exit 1 }
}

# --------------------------------------------------------------------------- #
# 启动数据层
# --------------------------------------------------------------------------- #
function Test-Port([int]$Port, [string]$Host_ = '127.0.0.1') {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect($Host_, $Port)
        return $true
    } catch { return $false } finally { $client.Dispose() }
}

Write-Step '启动数据层（PostgreSQL + Neo4j + Redis）'
if ((Test-Port 5432) -and (Test-Port 7687) -and (Test-Port 6379)) {
    Write-Ok '数据层已在运行（5432 / 7687 / 6379 均可达）'
} else {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) {
        Write-Err '本机未安装 Docker，无法启动数据层；请手动准备 PostgreSQL/Neo4j/Redis'
        exit 1
    }
    Push-Location $Deploy
    try {
        & docker compose -f docker-compose-infra.yml -p supervision-infra up -d
        if ($LASTEXITCODE -ne 0) { Write-Err '数据层启动失败，请检查 Docker 是否运行'; exit 1 }
    } finally { Pop-Location }

    Write-Host '  等待数据库就绪…' -ForegroundColor Gray
    $deadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $deadline) {
        if ((Test-Port 5432) -and (Test-Port 7687) -and (Test-Port 6379)) { break }
        Start-Sleep -Seconds 3
    }
    if (Test-Port 5432) { Write-Ok 'PostgreSQL 就绪 :5432' } else { Write-Warn2 'PostgreSQL 未就绪，后端可能启动失败' }
    if (Test-Port 7687) { Write-Ok 'Neo4j 就绪 :7687（图谱增强可用）' } else { Write-Warn2 'Neo4j 未就绪，检索将跳过图谱增强' }
}

# --------------------------------------------------------------------------- #
# 环境变量
# --------------------------------------------------------------------------- #
$env:PYTHONIOENCODING = 'utf-8'
$env:TMP = Join-Path $VarDir 'pip-tmp'
$env:TEMP = $env:TMP
New-Item -ItemType Directory -Force -Path $env:TMP | Out-Null

if ($Offline) {
    $env:SUPERVISION_LLM_PROVIDER = 'fake'
    Write-Ok '离线演示模式：LLM_PROVIDER=fake'
}

# --------------------------------------------------------------------------- #
# 可选：写入示例数据
# --------------------------------------------------------------------------- #
if ($Seed) {
    Write-Step '写入示例规范数据（幂等，已存在会跳过）'
    Push-Location $Backend
    try { & $python scripts\seed_data.py } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { Write-Warn2 '示例数据写入未成功，可稍后手动重试' }
}

# --------------------------------------------------------------------------- #
# 启动后端
# --------------------------------------------------------------------------- #
$started = @()
try {
    Write-Step "启动后端 :$ApiPort"
    $apiLog = Join-Path $LogDir 'api.log'
    $apiErr = Join-Path $LogDir 'api.err.log'
    $apiProc = Start-Process -FilePath $python `
        -ArgumentList @('-m', 'app.cli', '--host', '127.0.0.1', '--port', "$ApiPort", '--log-level', 'info') `
        -WorkingDirectory $Backend -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $apiLog -RedirectStandardError $apiErr
    $started += @{ Name = '后端'; Proc = $apiProc; Log = $apiLog; Err = $apiErr }

    $deadline = (Get-Date).AddSeconds(120)
    $apiReady = $false
    while ((Get-Date) -lt $deadline) {
        if ($apiProc.HasExited) { break }
        try {
            $null = Invoke-WebRequest -Uri "http://127.0.0.1:$ApiPort/api/v1/health" -TimeoutSec 4 -UseBasicParsing
            $apiReady = $true; break
        } catch { Start-Sleep -Seconds 3 }
    }
    if ($apiReady) {
        Write-Ok "后端就绪 http://127.0.0.1:$ApiPort/api/v1/docs"
    } else {
        Write-Err "后端启动失败，日志尾部："
        if (Test-Path $apiErr) { Get-Content $apiErr -Tail 15 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } }
        exit 1
    }

    # ----------------------------------------------------------------------- #
    # 启动前端
    # ----------------------------------------------------------------------- #
    if (-not $ApiOnly) {
        Write-Step "启动前端 :$WebPort"
        $webLog = Join-Path $LogDir 'web.log'
        $webErr = Join-Path $LogDir 'web.err.log'
        $webProc = Start-Process -FilePath $node `
            -ArgumentList @("`"$viteBin`"", '--port', "$WebPort", '--strictPort') `
            -WorkingDirectory $Frontend -PassThru -WindowStyle Hidden `
            -RedirectStandardOutput $webLog -RedirectStandardError $webErr
        $started += @{ Name = '前端'; Proc = $webProc; Log = $webLog; Err = $webErr }

        $deadline = (Get-Date).AddSeconds(180)
        $webReady = $false
        while ((Get-Date) -lt $deadline) {
            if ($webProc.HasExited) { break }
            try {
                $null = Invoke-WebRequest -Uri "http://127.0.0.1:$WebPort/" -TimeoutSec 4 -UseBasicParsing
                $webReady = $true; break
            } catch { Start-Sleep -Seconds 3 }
        }
        if ($webReady) {
            Write-Ok "前端就绪 http://127.0.0.1:$WebPort"
        } else {
            Write-Warn2 '前端启动超时，日志尾部：'
            if (Test-Path $webErr) { Get-Content $webErr -Tail 10 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } }
        }
    }

    # ----------------------------------------------------------------------- #
    # 汇总 + 前台等待
    # ----------------------------------------------------------------------- #
    Write-Step '启动完成'
    if (-not $ApiOnly) { Write-Host "  管理后台    http://127.0.0.1:$WebPort" -ForegroundColor White }
    Write-Host "  接口文档    http://127.0.0.1:$ApiPort/api/v1/docs" -ForegroundColor White
    Write-Host "  Neo4j 浏览器 http://127.0.0.1:7474" -ForegroundColor White
    Write-Host "  登录账号    admin / Admin@12345" -ForegroundColor White
    if (-not $Seed) {
        Write-Host "`n  提示：如需示例规范数据，重跑时加 -Seed（约 1 分钟）" -ForegroundColor Gray
    }
    Write-Host "`n  日志目录    $LogDir" -ForegroundColor Gray
    Write-Host '  按 Ctrl+C 停止全部服务' -ForegroundColor Yellow

    # 前台等待：任一子进程退出即结束，或用户 Ctrl+C
    while ($true) {
        Start-Sleep -Seconds 2
        $dead = $started | Where-Object { $_.Proc.HasExited }
        if ($dead) {
            Write-Warn2 ("以下进程已退出：" + (($dead | ForEach-Object { $_.Name }) -join '、'))
            break
        }
    }
} finally {
    Write-Step '停止服务'
    foreach ($item in $started) {
        $proc = $item.Proc
        if ($proc -and -not $proc.HasExited) {
            try {
                # 连同子进程一起结束（vite/node 会派生子进程）
                & taskkill /PID $proc.Id /T /F 2>$null | Out-Null
                Write-Ok ("已停止 " + $item.Name)
            } catch {
                Write-Warn2 ("停止 " + $item.Name + " 失败，请手动结束 PID " + $proc.Id)
            }
        }
    }
    Write-Host '  数据层容器保留运行；如需停止：docker compose -f deploy/docker-compose-infra.yml -p supervision-infra down' -ForegroundColor Gray
}
