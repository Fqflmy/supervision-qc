# 工程监理质量智能评估系统 —— 开发任务入口
#
# 用法：
#   .\scripts\dev.ps1 -Task setup          初始化数据层与示例数据
#   .\scripts\dev.ps1 -Task api            启动后端（读取 backend\.env）
#   .\scripts\dev.ps1 -Task api-offline    启动后端（Fake 模型，无 Key 也能跑通全流程）
#   .\scripts\dev.ps1 -Task web            启动前端开发服务器
#   .\scripts\dev.ps1 -Task test           运行 pytest
#   .\scripts\dev.ps1 -Task verify         运行全部自检（pytest + 检索 + 图谱 + 接口 + e2e）
#   .\scripts\dev.ps1 -Task browser-test   浏览器端到端（需在非受限环境执行）
#   .\scripts\dev.ps1 -Task build          前端生产构建
#   .\scripts\dev.ps1 -Task infra-up       启动 PostgreSQL/Neo4j/Redis
#   .\scripts\dev.ps1 -Task infra-down     停止数据层

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('setup', 'api', 'api-offline', 'web', 'test', 'verify', 'browser-test', 'build', 'infra-up', 'infra-down', 'stats')]
    [string]$Task
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$Deploy = Join-Path $Root 'deploy'

# 优先使用本机已验证的 conda 环境，其次使用 PATH 中的 python
$Python = if (Test-Path 'D:\Miniconda3\envs\test001\python.exe') {
    'D:\Miniconda3\envs\test001\python.exe'
} else {
    'python'
}

# 沙箱/受限环境下 pip 与 esbuild 的临时目录需落在工作区内
$env:TMP = Join-Path $Root 'var\pip-tmp'
$env:TEMP = $env:TMP
New-Item -ItemType Directory -Force -Path $env:TMP | Out-Null

function Invoke-Step([string]$Title, [scriptblock]$Action) {
    Write-Host "`n=== $Title ===" -ForegroundColor Cyan
    & $Action
}

switch ($Task) {
    'setup' {
        Invoke-Step '启动数据层容器' { Push-Location $Deploy; docker compose -f docker-compose-infra.yml up -d; Pop-Location }
        Invoke-Step '等待 PostgreSQL 就绪' {
            for ($i = 0; $i -lt 30; $i++) {
                $ok = (Test-NetConnection -ComputerName 127.0.0.1 -Port 5432 -InformationLevel Quiet -WarningAction SilentlyContinue)
                if ($ok) { Write-Host 'PostgreSQL 已就绪'; break }
                Start-Sleep -Seconds 2
            }
        }
        Invoke-Step '写入示例规范并建立索引' { Push-Location $Backend; & $Python scripts\seed_data.py; Pop-Location }
    }

    'api' {
        Invoke-Step '启动后端（读取 backend\.env，含 PostgreSQL 检查点）' {
            Push-Location $Backend
            & $Python -m app.cli --host 127.0.0.1 --port 8000
            Pop-Location
        }
    }

    'api-offline' {
        Invoke-Step '启动后端（Fake 模型，离线演示）' {
            $env:SUPERVISION_LLM_PROVIDER = 'fake'
            Push-Location $Backend
            & $Python -m app.cli --host 127.0.0.1 --port 8000
            Pop-Location
        }
    }

    'web' {
        Invoke-Step '启动前端开发服务器 :5173' {
            Push-Location $Frontend
            if (-not (Test-Path 'node_modules')) {
                $env:npm_config_cache = Join-Path $Frontend '.npm-cache'
                npm install --registry=https://registry.npmmirror.com --no-audit --no-fund --ignore-scripts
            }
            npm run dev
            Pop-Location
        }
    }

    'test' {
        Invoke-Step 'pytest 单元 + 集成测试' { Push-Location $Backend; & $Python -m pytest -q; Pop-Location }
    }

    'verify' {
        Push-Location $Backend
        Invoke-Step 'pytest' { & $Python -m pytest -q }
        Invoke-Step '检索管道自检' { & $Python scripts\smoke_retrieval.py }
        Invoke-Step '图谱自检' { $env:SUPERVISION_NEO4J_ENABLED = 'true'; & $Python scripts\smoke_graph.py }
        Invoke-Step '路由与 OpenAPI 校验' { & $Python scripts\smoke_api.py }
        Invoke-Step '全链路端到端（Fake 模型）' { & $Python scripts\e2e_check.py }
        Pop-Location
    }

    'browser-test' {
        Invoke-Step '浏览器端到端（需非受限环境）' { Push-Location $Backend; & $Python scripts\browser_e2e.py; Pop-Location }
    }

    'build' {
        Invoke-Step '前端类型检查与生产构建' {
            Push-Location $Frontend
            npx vue-tsc --noEmit
            npm run build
            Pop-Location
        }
    }

    'infra-up' {
        Push-Location $Deploy; docker compose -f docker-compose-infra.yml up -d; Pop-Location
        Write-Host '数据层已启动：PostgreSQL 5432 / Neo4j 7474,7687 / Redis 6379'
    }

    'infra-down' {
        Push-Location $Deploy; docker compose -f docker-compose-infra.yml down; Pop-Location
        Write-Host '数据层已停止（数据卷保留）'
    }

    'stats' {
        Push-Location $Backend; & $Python scripts\project_stats.py; Pop-Location
    }
}
