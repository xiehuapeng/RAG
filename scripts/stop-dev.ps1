param()

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$targets = @(
    @{
        Name = "backend"
        Port = 8000
        Match = @("knowledge_base_backend", "uvicorn", "app.main:app")
    },
    @{
        Name = "frontend"
        Port = 5173
        Match = @("knowledge_base_frontend", "vite", "npm.cmd")
    }
)

function Write-Step($message) {
    Write-Host "[dev-stop] $message" -ForegroundColor Yellow
}

function Get-ProcessInfoMap() {
    $map = @{}
    Get-CimInstance Win32_Process | ForEach-Object {
        $map[[int]$_.ProcessId] = $_
    }
    return $map
}

function Test-MatchKeywords($process, $keywords) {
    if (-not $process) {
        return $false
    }

    $haystack = @(
        $process.Name
        $process.CommandLine
    ) -join " "

    foreach ($keyword in $keywords) {
        if ($haystack -like "*$keyword*") {
            return $true
        }
    }

    return $false
}

function Get-StopRootId($processId, $processMap, $keywords) {
    $currentId = [int]$processId
    $lastMatchedId = $currentId

    while ($processMap.ContainsKey($currentId)) {
        $current = $processMap[$currentId]
        if (Test-MatchKeywords $current $keywords) {
            $lastMatchedId = $currentId
        }

        $parentId = [int]$current.ParentProcessId
        if (-not $parentId -or -not $processMap.ContainsKey($parentId)) {
            break
        }

        $parent = $processMap[$parentId]
        if (-not (Test-MatchKeywords $parent $keywords)) {
            break
        }

        $currentId = $parentId
        $lastMatchedId = $currentId
    }

    return $lastMatchedId
}

function Get-ProcessTreeIds($rootId, $processMap) {
    $result = New-Object System.Collections.Generic.List[int]
    $queue = New-Object System.Collections.Generic.Queue[int]
    $queue.Enqueue([int]$rootId)

    while ($queue.Count -gt 0) {
        $currentId = $queue.Dequeue()
        if ($result.Contains($currentId)) {
            continue
        }

        $result.Add($currentId)
        foreach ($process in $processMap.Values) {
            if ([int]$process.ParentProcessId -eq $currentId) {
                $queue.Enqueue([int]$process.ProcessId)
            }
        }
    }

    return $result
}

function Stop-ProcessTree($rootId, $processMap) {
    $treeIds = Get-ProcessTreeIds -rootId $rootId -processMap $processMap | Sort-Object -Descending
    foreach ($processId in $treeIds) {
        try {
            cmd /c "taskkill /PID $processId /T /F" > $null 2>&1
        } catch {
        }
    }
}

$processMap = Get-ProcessInfoMap
$stoppedAny = $false

foreach ($target in $targets) {
    $listeners = Get-NetTCPConnection -LocalPort $target.Port -State Listen -ErrorAction SilentlyContinue
    if (-not $listeners) {
        Write-Step "No $($target.Name) process is listening on port $($target.Port)"
        continue
    }

    $rootIds = @()
    foreach ($listener in $listeners) {
        $rootIds += Get-StopRootId -processId $listener.OwningProcess -processMap $processMap -keywords $target.Match
    }

    $rootIds = $rootIds | Sort-Object -Unique
    foreach ($rootId in $rootIds) {
        Write-Step "Stopping $($target.Name) process tree rooted at PID $rootId"
        Stop-ProcessTree -rootId $rootId -processMap $processMap
        $stoppedAny = $true
    }
}

if (-not $stoppedAny) {
    Write-Host "No matching dev processes were running." -ForegroundColor Green
} else {
    Write-Host "Frontend and backend dev processes have been stopped." -ForegroundColor Green
}
