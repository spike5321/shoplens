<#
.SYNOPSIS
    ShopLens 本地 PostgreSQL 实例管理（免安装、免管理员权限）。

.DESCRIPTION
    本机没有 Docker，也没有系统级 PostgreSQL。替代路径是使用 PostgreSQL 官方
    发布的 Windows 免安装二进制包（EnterpriseDB "binaries" zip），在用户目录下
    initdb 出一个只监听 127.0.0.1 的独立实例。

    这样做的好处：
    * 不需要管理员权限，不注册 Windows 服务，不影响机器上任何已有数据库；
    * 用非默认端口 55432，避免和别的实例撞端口；
    * 数据目录在 .local/pgdata，已被 .gitignore 忽略，删掉即可完全清理。

    重要：这是真实的 PostgreSQL 服务端进程，不是 SQLite，也不是内存模拟。
    阶段 1/2 的权限、时区、超时、持久化验证都在它上面做。

    前置条件：把官方二进制包解压到 .local/pgsql（见 docs/decisions.md 的下载命令）。

.EXAMPLE
    ./scripts/pg.ps1 init      # 初始化 + 启动 + 建库 + 写 .env
    ./scripts/pg.ps1 status
    ./scripts/pg.ps1 psql
    ./scripts/pg.ps1 stop
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet('init', 'start', 'stop', 'status', 'psql', 'reset')]
    [string]$Action,

    [int]$Port = 55432,
    [string]$DatabaseName = 'shoplens',
    [string]$SuperUser = 'postgres'
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$PgHome   = Join-Path $RepoRoot '.local\pgsql'
$PgData   = Join-Path $RepoRoot '.local\pgdata'
$LogFile  = Join-Path $RepoRoot '.local\pg.log'
$PassFile = Join-Path $RepoRoot '.local\pgadmin.pass'
$EnvFile  = Join-Path $RepoRoot '.env'

function Get-PgBin([string]$Name) {
    $exe = Join-Path $PgHome "bin\$Name.exe"
    if (-not (Test-Path $exe)) {
        throw "找不到 $exe。请先按 docs/decisions.md 把 PostgreSQL 官方二进制包解压到 .local\pgsql"
    }
    return $exe
}

function New-RandomPassword([int]$Length = 24) {
    # 只用 URL 安全的字符集，避免密码里的特殊字符干扰连接串
    $alphabet = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789-_'
    $bytes = New-Object byte[] $Length
    # 注意：Windows PowerShell 5.1 跑在 .NET Framework 上，
    # 没有 RandomNumberGenerator.Fill（那是 .NET Core 3.0+ 的静态方法）。
    # 用 Create().GetBytes() 兼容两个运行时。
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }
    -join ($bytes | ForEach-Object { $alphabet[$_ % $alphabet.Length] })
}

function Get-OrCreatePassword {
    if (Test-Path $PassFile) {
        return (Get-Content -Path $PassFile -Raw).Trim()
    }
    $pw = New-RandomPassword
    # 不含 BOM，避免把 BOM 当成密码的一部分
    [System.IO.File]::WriteAllText($PassFile, $pw, (New-Object System.Text.UTF8Encoding($false)))
    return $pw
}

function Write-EnvFile([string]$Password) {
    if (Test-Path $EnvFile) {
        Write-Host "[skip] .env 已存在，未覆盖（如需重写请先手动删除）" -ForegroundColor DarkGray
        return
    }
    $content = @"
# 由 scripts/pg.ps1 init 生成。.env 已被 .gitignore 忽略，不要提交。
SHOPLENS_ENV=local

SHOPLENS_PG_HOST=127.0.0.1
SHOPLENS_PG_PORT=$Port
SHOPLENS_PG_DB=$DatabaseName

SHOPLENS_PG_ADMIN_USER=$SuperUser
SHOPLENS_PG_ADMIN_PASSWORD=$Password

# app / reader 角色在阶段 1/2 建立，这里先留空
SHOPLENS_PG_APP_USER=shoplens_app
SHOPLENS_PG_APP_PASSWORD=
SHOPLENS_PG_READER_USER=analytics_reader
SHOPLENS_PG_READER_PASSWORD=

# 默认离线假模型：不需要任何 Key
SHOPLENS_MODEL_PROVIDER=offline
SHOPLENS_DEEPSEEK_API_KEY=
SHOPLENS_DEEPSEEK_MODEL=deepseek-chat
SHOPLENS_DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

SHOPLENS_MAX_REPAIR_COUNT=1
SHOPLENS_SQL_STATEMENT_TIMEOUT_MS=3000
SHOPLENS_SQL_MAX_ROWS=200

SHOPLENS_DEMO_REFERENCE_DATE=2026-09-30
SHOPLENS_BUSINESS_TIMEZONE=Asia/Shanghai
"@
    [System.IO.File]::WriteAllText($EnvFile, $content, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "[ok] 已生成 .env（含本地随机生成的数据库密码）" -ForegroundColor Green
}

function Test-Running {
    $pg_ctl = Get-PgBin 'pg_ctl'
    if (-not (Test-Path $PgData)) { return $false }
    & $pg_ctl -D $PgData status *> $null
    return ($LASTEXITCODE -eq 0)
}

function Invoke-Init {
    if (Test-Path $PgData) {
        Write-Host "[skip] 数据目录已存在: $PgData" -ForegroundColor DarkGray
    } else {
        $initdb = Get-PgBin 'initdb'
        $password = Get-OrCreatePassword
        $tmpPw = Join-Path $RepoRoot '.local\initpw.tmp'
        [System.IO.File]::WriteAllText($tmpPw, $password, (New-Object System.Text.UTF8Encoding($false)))

        New-Item -ItemType Directory -Force -Path (Split-Path $PgData) | Out-Null
        Write-Host "[run] initdb -> $PgData" -ForegroundColor Cyan
        & $initdb `
            -D $PgData `
            -U $SuperUser `
            -E UTF8 `
            --locale=C `
            --auth-local=trust `
            --auth-host=scram-sha-256 `
            --pwfile=$tmpPw
        $code = $LASTEXITCODE
        Remove-Item $tmpPw -Force -ErrorAction SilentlyContinue
        if ($code -ne 0) { throw "initdb 失败，退出码 $code" }

        # 只监听回环地址；端口刻意避开 5432；服务端时区固定 UTC，
        # 业务时区由应用层按 Asia/Shanghai 解释（见 PROJECT_PLAN.md 第 3 节）。
        #
        # 注意：initdb 会按机器时区往 postgresql.conf 写一行
        #   timezone = 'Asia/Shanghai'
        # 如果我们也往同一文件追加 timezone，就会出现两行、靠"后者生效"来碰运气。
        # 所以这里把覆盖项写进独立文件，再用 include 在末尾引入（include 在文件末尾，
        # 因此其中的设置优先级最高，也不会和生成值混在一起）。
        $overrideConf = Join-Path $PgData 'shoplens.conf'
        [System.IO.File]::WriteAllText($overrideConf, @"
# ShopLens 本地实例覆盖配置（由 scripts/pg.ps1 init 生成）
# 该文件被 postgresql.conf 末尾 include 引入，优先级最高。

# 只监听回环地址，不对外暴露
listen_addresses = '127.0.0.1'
# 刻意避开默认 5432，避免与机器上其它 PostgreSQL 实例冲突
port = $Port
# 服务端存 timestamptz，时区固定 UTC；业务时区 Asia/Shanghai 由应用层解释
timezone = 'UTC'
log_timezone = 'UTC'
# 记录全部语句，便于阶段 2 验证"权限拒绝真的发生了"
log_statement = 'all'
"@, (New-Object System.Text.UTF8Encoding($false)))

        $conf = Join-Path $PgData 'postgresql.conf'
        Add-Content -Path $conf -Value @"

# ---- ShopLens 本地实例覆盖配置（由 scripts/pg.ps1 写入）----
# 覆盖项在单独文件里，避免与 initdb 生成的默认值混在一起。
include = 'shoplens.conf'
"@
        Write-Host "[ok] initdb 完成" -ForegroundColor Green
        Write-EnvFile $password
    }

    if (Test-Running) {
        Write-Host "[skip] 实例已在运行" -ForegroundColor DarkGray
    } else {
        $pg_ctl = Get-PgBin 'pg_ctl'
        Write-Host "[run] 启动 PostgreSQL，日志 $LogFile" -ForegroundColor Cyan
        & $pg_ctl -D $PgData -l $LogFile -w start
        if ($LASTEXITCODE -ne 0) { throw "启动失败，请看 $LogFile" }
    }

    # 建库（幂等）
    $psql = Get-PgBin 'psql'
    $env:PGPASSWORD = Get-OrCreatePassword
    $exists = & $psql -h 127.0.0.1 -p $Port -U $SuperUser -d postgres -tAc `
        "SELECT 1 FROM pg_database WHERE datname = '$DatabaseName'"
    if ($exists -ne '1') {
        Write-Host "[run] 创建数据库 $DatabaseName" -ForegroundColor Cyan
        & $psql -h 127.0.0.1 -p $Port -U $SuperUser -d postgres -c "CREATE DATABASE $DatabaseName"
    }
    Write-Host "[ok] 数据库就绪: postgresql://$SuperUser@127.0.0.1:$Port/$DatabaseName" -ForegroundColor Green
}

function Invoke-Start {
    if (-not (Test-Path $PgData)) { throw "尚未初始化，请先运行: ./scripts/pg.ps1 init" }
    if (Test-Running) { Write-Host "[skip] 已在运行"; return }
    $pg_ctl = Get-PgBin 'pg_ctl'
    & $pg_ctl -D $PgData -l $LogFile -w start
    if ($LASTEXITCODE -ne 0) { throw "启动失败，请看 $LogFile" }
    Write-Host "[ok] 已启动" -ForegroundColor Green
}

function Invoke-Stop {
    if (-not (Test-Path $PgData)) { throw "数据目录不存在: $PgData" }
    if (-not (Test-Running)) { Write-Host "[skip] 未在运行"; return }
    $pg_ctl = Get-PgBin 'pg_ctl'
    & $pg_ctl -D $PgData -m fast -w stop
    Write-Host "[ok] 已停止" -ForegroundColor Green
}

function Invoke-Status {
    $pg_ctl = Get-PgBin 'pg_ctl'
    if (-not (Test-Path $PgData)) {
        Write-Host "状态: 未初始化（$PgData 不存在）" -ForegroundColor Yellow
        return
    }
    Write-Host "数据目录: $PgData"
    & $pg_ctl -D $PgData status
    if ($LASTEXITCODE -ne 0) {
        Write-Host "状态: 未运行" -ForegroundColor Yellow
        return
    }
    $psql = Get-PgBin 'psql'
    $env:PGPASSWORD = Get-OrCreatePassword
    & $psql -h 127.0.0.1 -p $Port -U $SuperUser -d $DatabaseName `
        -c "SELECT version() AS server_version, current_setting('TimeZone') AS session_tz, now() AS server_now"
}

function Invoke-Psql {
    $psql = Get-PgBin 'psql'
    $env:PGPASSWORD = Get-OrCreatePassword
    & $psql -h 127.0.0.1 -p $Port -U $SuperUser -d $DatabaseName
}

function Invoke-Reset {
    Write-Host "这会删除 $PgData 里的全部本地数据（只影响本项目的演示实例）" -ForegroundColor Yellow
    if (Test-Running) { Invoke-Stop }
    if (Test-Path $PgData) { Remove-Item -Recurse -Force $PgData }
    # 刻意保留 $PassFile：重新 init 时复用同一个密码，
    # 这样已经写好的 .env 不会因为重置而失效。
    Write-Host "[ok] 已清理，可重新运行 init（数据库密码保持不变）" -ForegroundColor Green
}

switch ($Action) {
    'init'   { Invoke-Init }
    'start'  { Invoke-Start }
    'stop'   { Invoke-Stop }
    'status' { Invoke-Status }
    'psql'   { Invoke-Psql }
    'reset'  { Invoke-Reset }
}
