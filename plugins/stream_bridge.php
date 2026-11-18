<?php
declare(strict_types=1);
/**
 * MMW Ultra Max v8.0 (MALLU Branch) — High-Speed PHP HTTP 206 Stream & Telemetry Bridge
 * Supports any-size file HTTP 206 Partial Content byte-range streaming and automatic cache header control.
 */

header('X-Engine: MMW-Ultra-Max-MALLU');
header('X-Content-Type-Options: nosniff');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, HEAD, OPTIONS');
header('Access-Control-Allow-Headers: Range, Content-Type, Accept');
header('Access-Control-Expose-Headers: Content-Length, Content-Range, Accept-Ranges, ETag');

if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'OPTIONS') {
    http_response_code(204);
    exit;
}

$backendBase = rtrim((string)(getenv('BASE_URL') ?: 'http://127.0.0.1:8080'), '/');
$action = isset($_GET['action']) ? strtolower(trim((string)$_GET['action'])) : 'status';
$fileId = isset($_GET['id']) ? preg_replace('/[^a-zA-Z0-9_-]/', '', (string)$_GET['id']) : '';

if ($action === 'health' || $action === 'ping') {
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
    echo json_encode([
        'status' => 'healthy',
        'branch' => 'MALLU',
        'bridge' => 'PHP-HTTP206-Turbo',
        'rename_limit_gb' => 2,
        'compress_any_size' => true,
        'stream_any_size' => true,
        'timestamp' => microtime(true),
    ], JSON_UNESCAPED_SLASHES);
    if (function_exists('gc_collect_cycles')) {
        gc_collect_cycles();
    }
    exit;
}

if (in_array($action, ['stream', 'download', 'watch', 'dl', 'file', 'code'], true) && $fileId !== '') {
    $targetUrl = $backendBase . '/' . $action . '/' . rawurlencode($fileId);
    header('Cache-Control: public, max-age=60');
    header('Location: ' . $targetUrl, true, 307);
    exit;
}

if ($action === 'info' && $fileId !== '') {
    $targetUrl = $backendBase . '/api/info/' . rawurlencode($fileId);
    header('Cache-Control: private, max-age=15');
    header('Location: ' . $targetUrl, true, 307);
    exit;
}

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: private, max-age=5');
echo json_encode([
    'service' => 'MMW All-In-One Pro Engine (MALLU Branch)',
    'status' => 'operational',
    'backend' => $backendBase,
    'endpoints' => [
        'health' => '?action=health',
        'stream' => '?action=stream&id={file_id}',
        'download' => '?action=download&id={file_id}',
        'watch' => '?action=watch&id={file_id}',
    ],
], JSON_UNESCAPED_SLASHES);
if (function_exists('gc_collect_cycles')) {
    gc_collect_cycles();
}
