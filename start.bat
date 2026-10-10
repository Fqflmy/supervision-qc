@echo off
chcp 65001 >nul 2>&1
setlocal
rem ===========================================================================
rem  工程监理质量智能评估系统 —— 一键启动（Windows 双击即可运行）
rem
rem  等价于：powershell -ExecutionPolicy Bypass -File start.ps1
rem  -ExecutionPolicy Bypass 只对本次调用生效，不修改系统执行策略。
rem
rem  可用参数（会原样透传给 start.ps1）：
rem    -ApiKey sk-xxxx     指定 DeepSeek Key
rem    -WithData           启动并写入示例规范数据
rem    -Offline            离线演示模式（不调用真实大模型）
rem    -Mode infra         只启动数据层
rem    -Build              强制重新构建镜像
rem    -Down               停止所有服务
rem
rem  例：start.bat -ApiKey sk-xxxx -WithData
rem ===========================================================================

set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%start.ps1" %*
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
  echo.
  echo [X] 脚本以退出码 %EXITCODE% 结束，请查看上方输出排查。
)

if "%~1"=="" (
  echo.
  pause
)
exit /b %EXITCODE%
