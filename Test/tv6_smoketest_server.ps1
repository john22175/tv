$ErrorActionPreference = "Stop"

$hostIp = "10.171.64.144"
$port = 65331
$alias = "6"
$mediaPath = "C:\Users\hipes\OneDrive\Desktop\Work\TV\Sources\SiteService Short.gif"
$logPath = "C:\Users\hipes\OneDrive\Desktop\Work\TV\tv6_smoketest_hits.log"
$mediaName = [System.IO.Path]::GetFileName($mediaPath)
$mediaRoute = "/media/" + [System.Uri]::EscapeDataString($mediaName)
$stateRoute = "/receiver-state-alias/$alias"

Set-Content -LiteralPath $logPath -Value "Serving TV 6 smoke test on http://$hostIp`:$port" -Encoding UTF8

$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add("http://*:$port/")
$listener.Start()

try {
    while ($listener.IsListening) {
        $context = $listener.GetContext()
        $request = $context.Request
        $response = $context.Response
        Add-Content -LiteralPath $logPath -Value ("{0} {1}" -f $request.HttpMethod, $request.RawUrl) -Encoding UTF8

        if ($request.RawUrl -eq $stateRoute) {
            $json = @{
                source_name = $mediaName
                note = "TV 6 smoke test"
                mime_type = "image/gif"
                media_url = "http://$hostIp`:$port$mediaRoute"
            } | ConvertTo-Json -Compress
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
            $response.StatusCode = 200
            $response.ContentType = "application/json"
            $response.ContentLength64 = $bytes.Length
            $response.OutputStream.Write($bytes, 0, $bytes.Length)
            $response.Close()
            continue
        }

        if ($request.RawUrl -eq $mediaRoute -and (Test-Path -LiteralPath $mediaPath)) {
            $bytes = [System.IO.File]::ReadAllBytes($mediaPath)
            $response.StatusCode = 200
            $response.ContentType = "image/gif"
            $response.ContentLength64 = $bytes.Length
            $response.OutputStream.Write($bytes, 0, $bytes.Length)
            $response.Close()
            continue
        }

        $response.StatusCode = 404
        $response.Close()
    }
}
finally {
    $listener.Stop()
    $listener.Close()
}
