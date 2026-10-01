<#
.SYNOPSIS
    工程监理质量智能评估系统 —— 一键启动脚本（全容器方案，Windows / PowerShell）

.DESCRIPTION
    自动完成：环境检查 → 准备 deploy\.env → 构建镜像 → 启动服务 → 等待健康 →
    （可选）写入示例规范数据 → 打印访问地址。

.EXAMPLE
    .\start.ps1                          # 启动（读取 deploy\.env）
    .\start.ps1 -ApiKey sk-xxxxxxxx      # 指定 DeepSeek Key 并启动
    .\start.ps1 -WithData                # 启动并写入示例规范数据
    .\start.ps1 -Mode infra              # 只启动数据层（配合本机后端开发）
    .\start.ps1 -Offline                 # 离线演示模式（不调用真实大模型）
    .\start.ps1 -Build                   # 强制重新构建镜像
    .\start.ps1 -Down                    # 停止所有服务

.NOTES
    访问：Web http://localhost:8080   API http://localhost:8000/api/v1/docs
    日志：logs\compose-up.log（启动过程）、docker compose logs -f api（运行日志）
    若提示「未对文件进行数字签名」，请双击 start.bat，或使用：
        powershell -ExecutionPolicy Bypass -File .\start.ps1
#>
[CmdletBinding()]
param(
    [ValidateSet('all', 'infra')]
    [string]$Mode = 'all',

    [string]$ApiKey,

    [switch]$WithData,
    [switch]$Build,
    [switch]$Down,
    [switch]$Offline,
    [switch]$NoWait
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Deploy = Join-Path $Root 'deploy'
$EnvFile = Join-Path $Deploy '.env'
$EnvExample = Join-Path $Deploy '.env.example'
$InfraCompose = Join-Path $Deploy 'docker-compose-infra.yml'
$LogDir = Join-Path $Root 'logs'

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Step([string]$Text) { Write-Host "`n=== $Text ===" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "  [OK] $Text" -ForegroundColor Green }
function Write-Warn2([string]$Text) { Write-Host "  [!]  $Text" -ForegroundColor Yellow }
function Write-Err([string]$Text) { Write-Host "  [X] $Text" -ForegroundColor Red }

# docker 会把镜像下载/构建进度写到 stderr；在 $ErrorActionPreference='Stop' 下
# PowerShell 会把这类输出当成终止性错误，导致 docker 明明成功、脚本却提前退出。
# 因此所有 docker 调用统一走这个封装：输出落盘，只用退出码判定成败。
function Invoke-Docker {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [string]$LogName,
        [switch]$Quiet
    )

    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ($LogName) {
            & docker @Arguments *> (Join-Path $Root $LogName)
        } elseif ($Quiet) {
            & docker @Arguments *> $null
        } else {
            & docker @Arguments 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
        }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    return $code
}

# --------------------------------------------------------------------------- #
# 0. 环境检查
# --------------------------------------------------------------------------- #
Write-Step '环境检查'

$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCmd) {
    Write-Err '未找到 docker 命令，请先安装 Docker Desktop'
    exit 1
}
Write-Ok "docker: $($dockerCmd.Source)"

$serverVersion = $null
try { $serverVersion = & docker version --format '{{.Server.Version}}' 2>$null } catch { }
if (-not $serverVersion) {
    Write-Err 'Docker 守护进程未运行，请先启动 Docker Desktop 后重试'
    exit 1
}
Write-Ok "Docker 引擎: $serverVersion"

$composeVersion = $null
try { $composeVersion = & docker compose version --short 2>$null } catch { }
if (-not $composeVersion) {
    Write-Err 'docker compose（v2）不可用，请升级 Docker Desktop'
    exit 1
}
Write-Ok "Docker Compose: $composeVersion"

# --------------------------------------------------------------------------- #
# 停止模式
# --------------------------------------------------------------------------- #
if ($Down) {
    Write-Step '停止服务'
    $null = Invoke-Docker -Arguments @('compose', 'down')
    if (Test-Path $InfraCompose) {
        $null = Invoke-Docker -Arguments @('compose', '-f', $InfraCompose, '-p', 'supervision-infra', 'down') -Quiet
    }
    Write-Ok '服务已停止（数据卷保留；如需彻底清理请执行 docker volume prune）'
    exit 0
}

# --------------------------------------------------------------------------- #
# 1. 准备 .env
# --------------------------------------------------------------------------- #
Write-Step '准备配置（deploy\.env）'
if (-not (Test-Path $EnvFile)) {
    if (-not (Test-Path $EnvExample)) {
        Write-Err "缺少 $EnvExample，无法生成配置"
        exit 1
    }
    Copy-Item $EnvExample $EnvFile
    Write-Ok '已从 .env.example 生成 deploy\.env'
} else {
    Write-Ok 'deploy\.env 已存在，沿用现有配置'
}

# 用无 BOM UTF-8 读写，避免首行配置解析失败
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$lines = [System.IO.File]::ReadAllLines($EnvFile, $utf8NoBom)

function Set-EnvLine([string[]]$Source, [string]$Key, [string]$Value) {
    $found = $false
    for ($i = 0; $i -lt $Source.Count; $i++) {
        if ($Source[$i] -match "^[ \t]*$([regex]::Escape($Key))=") {
            $Source[$i] = "$Key=$Value"
            $found = $true
        }
    }
    if (-not $found) { $Source += "$Key=$Value" }
    return $Source
}

if ($ApiKey) {
    $lines = Set-EnvLine $lines 'DEEPSEEK_API_KEY' $ApiKey
    Write-Ok '已写入 DeepSeek API Key'
}

if ($Offline) {
    $lines = Set-EnvLine $lines 'LLM_PROVIDER' 'fake'
    Write-Ok '已切换为离线演示模式（LLM_PROVIDER=fake，不调用真实大模型）'
} else {
    $keyNow = ($lines | Where-Object { $_ -match '^DEEPSEEK_API_KEY=' }) -replace '^DEEPSEEK_API_KEY=', ''
    if ([string]::IsNullOrWhiteSpace($keyNow)) {
        Write-Warn2 'DEEPSEEK_API_KEY 为空：LLM 相关功能（问答/Agent/Judge）会失败'
        Write-Warn2 '可执行 .\start.ps1 -ApiKey sk-xxxx，或编辑 deploy\.env；也可用 -Offline 走离线演示'
    } else {
        Write-Ok "DeepSeek API Key 已配置（$($keyNow.Substring(0, [Math]::Min(7, $keyNow.Length)))***）"
    }
}

$silicon = ($lines | Where-Object { $_ -match '^SILICONFLOW_API_KEY=' }) -replace '^SILICONFLOW_API_KEY=', ''
if ([string]::IsNullOrWhiteSpace($silicon) -and -not $Offline) {
    Write-Warn2 'SILICONFLOW_API_KEY 为空：Embedding/Reranker 将降级为哈希向量 + 分数融合重排'
}

[System.IO.File]::WriteAllLines($EnvFile, $lines, $utf8NoBom)

# --------------------------------------------------------------------------- #
# 2. 只启动数据层
# --------------------------------------------------------------------------- #
if ($Mode -eq 'infra') {
    Write-Step '启动数据层（PostgreSQL + Neo4j + Redis）'
    $code = Invoke-Docker -Arguments @('compose', '-f', $InfraCompose, '-p', 'supervision-infra', 'up', '-d') `
        -LogName 'logs\compose-infra-up.log'
    if ($code -ne 0) {
        Write-Err '数据层启动失败，详见 logs\compose-infra-up.log'
        exit 1
    }
    Write-Ok '数据层已启动：PostgreSQL 5432 / Neo4j 7474,7687 / Redis 6379'
    Write-Host "`n本地开发请接着执行：.\start-local.ps1 -ApiOnly" -ForegroundColor Gray
    exit 0
}

# --------------------------------------------------------------------------- #
# 3. 启动全部服务
# --------------------------------------------------------------------------- #
Write-Step '构建并启动全部服务'
Write-Host '  首次运行需构建镜像，可能耗时数分钟；进度写入 logs\compose-up.log' -ForegroundColor Gray

$upArgs = @('compose', 'up', '-d')
if ($Build) { $upArgs += '--build' }

$code = Invoke-Docker -Arguments $upArgs -LogName 'logs\compose-up.log'
if ($code -ne 0) {
    Write-Err '服务启动失败，详见 logs\compose-up.log（末尾通常有具体原因）'
    Write-Host '  排查：docker compose logs --tail 50 api' -ForegroundColor Gray
    exit 1
}
Write-Ok '容器已创建'

# --------------------------------------------------------------------------- #
# 4. 等待健康检查
# --------------------------------------------------------------------------- #
if (-not $NoWait) {
    Write-Step '等待服务就绪'
    $deadline = (Get-Date).AddMinutes(6)
    $apiReady = $false
    $webReady = $false
    while ((Get-Date) -lt $deadline) {
        if (-not $apiReady) {
            try {
                $null = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/v1/health' -TimeoutSec 4 -UseBasicParsing
                $apiReady = $true
                Write-Ok '后端 API 就绪 :8000'
            } catch { }
        }
        if (-not $webReady) {
            try {
                $null = Invoke-WebRequest -Uri 'http://127.0.0.1:8080/' -TimeoutSec 4 -UseBasicParsing
                $webReady = $true
                Write-Ok '前端 Web 就绪 :8080'
            } catch { }
        }
        if ($apiReady -and $webReady) { break }
        Start-Sleep -Seconds 5
    }
    if (-not $apiReady) { Write-Warn2 '后端 6 分钟内未就绪，请执行 docker compose logs api 排查' }
    if (-not $webReady) { Write-Warn2 '前端 6 分钟内未就绪，首次构建较慢可稍后刷新页面' }
}

# --------------------------------------------------------------------------- #
# 5. 数据初始化状态（由 api 容器入口自动完成）
# --------------------------------------------------------------------------- #
Write-Step '数据初始化检查'
$seedInfo = (& docker logs supervision-api 2>&1 | Select-String -Pattern '\[entrypoint\]|\[DONE\] 本次写入|已存在（doc_id' | Select-Object -Last 3)
if ($seedInfo) {
    foreach ($line in $seedInfo) { Write-Host "  $($line.Line.Trim())" -ForegroundColor DarkGray }
}
$ready = (& docker logs supervision-api 2>&1 | Select-String -Pattern '\[entrypoint\] 初始化完成|\[DONE\] 本次写入')
if ($ready) {
    Write-Ok '示例规范与向量索引已就绪'
} else {
    Write-Warn2 '初始化仍在进行或未成功，可执行：docker compose exec api python scripts/seed_data.py'
}

# --------------------------------------------------------------------------- #
# 6. 汇总
# --------------------------------------------------------------------------- #
Write-Step '启动完成'
Write-Host '  管理后台     http://localhost:8080' -ForegroundColor White
Write-Host '  接口文档     http://localhost:8000/api/v1/docs' -ForegroundColor White
Write-Host '  Neo4j 浏览器  http://localhost:7476' -ForegroundColor White
Write-Host '  登录账号     admin / Admin@12345' -ForegroundColor White
Write-Host "`n  查看状态   docker compose ps" -ForegroundColor Gray
Write-Host '  查看日志   docker compose logs -f api' -ForegroundColor Gray
Write-Host '  停止服务   .\start.ps1 -Down' -ForegroundColor Gray
