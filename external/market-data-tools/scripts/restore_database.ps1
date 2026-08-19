param(
    [string]$DatabaseHost,
    [int]$DatabasePort,
    [string]$DatabaseName,
    [string]$DatabaseUser,
    [string]$PsqlCommand = "psql",
    [switch]$AllowExistingServerDatabase
)

# 从 tools/.env 读取数据库配置；系统环境变量优先，方便云数据库部署覆盖本地值。
$toolsRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $toolsRoot ".env"
if (Test-Path $envFile) {
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)\s*$') {
            $key = $matches[1]
            $value = $matches[2].Trim().Trim('"').Trim("'")
            if (-not (Get-Item "Env:$key" -ErrorAction SilentlyContinue)) {
                Set-Item "Env:$key" $value
            }
        }
    }
}

# 参数优先于 .env，.env 优先于脚本默认值。这样直接运行脚本时不会因为
# PowerShell 在解析 param 默认值时尚未加载 .env，而意外落回旧数据库配置。
$DatabaseHost = if ($DatabaseHost) { $DatabaseHost } elseif ($env:AI_SEARCH_DB_HOST) { $env:AI_SEARCH_DB_HOST } else { "127.0.0.1" }
$DatabasePort = if ($DatabasePort -gt 0) { $DatabasePort } elseif ($env:AI_SEARCH_DB_PORT) { [int]$env:AI_SEARCH_DB_PORT } else { 15433 }
$DatabaseName = if ($DatabaseName) { $DatabaseName } elseif ($env:AI_SEARCH_DB_NAME) { $env:AI_SEARCH_DB_NAME } else { "icbc_shared" }
$DatabaseUser = if ($DatabaseUser) { $DatabaseUser } elseif ($env:AI_SEARCH_DB_USER) { $env:AI_SEARCH_DB_USER } else { "icbc_collab" }

# icbc_shared 是服务器上已经完成初始化并正在使用的正式数据库。默认拒绝恢复，
# 防止误把快照写回服务器；只有操作者明确传入确认开关时才允许执行灾备恢复。
if ($DatabaseName -eq "icbc_shared" -and -not $AllowExistingServerDatabase) {
    throw "检测到正式服务器数据库 icbc_shared，日常不需要恢复。若确需执行灾备恢复，请显式传入 -AllowExistingServerDatabase。"
}

$password = $env:AI_SEARCH_DB_PASSWORD
if (-not $password) {
    throw "未找到 AI_SEARCH_DB_PASSWORD，请先填写 tools/.env"
}

$env:PGPASSWORD = $password
$sqlFile = Join-Path $toolsRoot "database\full_database.sql"
if (-not (Test-Path $sqlFile)) {
    throw "找不到数据库快照：$sqlFile"
}

Write-Host "正在恢复数据库 $DatabaseName@$DatabaseHost`:$DatabasePort ..."
& $PsqlCommand -h $DatabaseHost -p $DatabasePort -U $DatabaseUser -d $DatabaseName -v ON_ERROR_STOP=1 -f $sqlFile
if ($LASTEXITCODE -ne 0) {
    throw "数据库恢复失败，psql exit code=$LASTEXITCODE"
}
Write-Host "数据库恢复完成。"
