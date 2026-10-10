@echo off
chcp 65001 >nul 2>&1
setlocal
rem ===========================================================================
rem  工程监理质量智能评估系统 —— 本机一键启动（数据层用 Docker，应用跑本机进程）
rem
rem  等价于：powershell -ExecutionPolicy Bypass -File start-local.ps1
rem  适用于本机已有 Python/Node 依赖，或 Docker Hub 不可达无法构建应用镜像的环境。
rem
rem  可用参数：
rem    -Offline    离线演示模式（Fake 模型，无需 API Key）
rem    -Seed       启动前写入示例规范数据
rem    -ApiOnly    只启动后端
rem
rem  例：start-local.bat -Seed
rem ===========================================================================

set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%start-local.ps1" %*
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
  echo.
  echo [X] 脚本以退出码 %EXITCODE% 结束，请查看上方输出排查。
)

echo.
pause
exit /b %EXITCODE%
