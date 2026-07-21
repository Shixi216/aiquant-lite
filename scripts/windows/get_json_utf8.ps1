[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^https?://")]
    [string]$Uri,

    [ValidateRange(1, 600)]
    [int]$TimeoutSeconds = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Net.Http

$utf8 = New-Object System.Text.UTF8Encoding(
    $false
)

[Console]::OutputEncoding = $utf8
$global:OutputEncoding = $utf8

$handler = New-Object System.Net.Http.HttpClientHandler
$handler.UseProxy = $false

$client = New-Object System.Net.Http.HttpClient(
    $handler
)

$client.Timeout = [TimeSpan]::FromSeconds(
    $TimeoutSeconds
)

try {
    $response = $client.GetAsync(
        $Uri
    ).GetAwaiter().GetResult()

    $bytes = $response.Content.ReadAsByteArrayAsync(
    ).GetAwaiter().GetResult()

    $jsonText = [System.Text.Encoding]::UTF8.GetString(
        $bytes
    )

    if (-not $response.IsSuccessStatusCode) {
        $previewLength = [Math]::Min(
            500,
            $jsonText.Length
        )

        $preview = $jsonText.Substring(
            0,
            $previewLength
        )

        throw (
            "HTTP request failed: " +
            [int]$response.StatusCode +
            " " +
            $response.ReasonPhrase +
            "; response=" +
            $preview
        )
    }

    if ([string]::IsNullOrWhiteSpace($jsonText)) {
        throw "HTTP response body is empty."
    }

    try {
        $jsonText | ConvertFrom-Json
    }
    catch {
        throw (
            "HTTP response is not valid JSON: " +
            $_.Exception.Message
        )
    }
}
finally {
    $client.Dispose()
    $handler.Dispose()
}