package com.ig.miniprinter

import android.annotation.SuppressLint
import android.bluetooth.*
import android.bluetooth.le.BluetoothLeScanner
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.content.Context
import android.graphics.*
import android.graphics.pdf.PdfRenderer
import android.os.Build
import android.os.ParcelFileDescriptor
import androidx.annotation.RequiresPermission
import kotlinx.coroutines.*
import java.io.File
import java.util.UUID

// ─────────────────────────────────────────────────────────────────────────────
// Usage example:
//
//   val printer = TiMiniPrinter(context)
//
//   // Scan and connect
//   printer.scan { devices -> /* devices: List<ScannedPrinter> */ }
//   printer.connect("F464B34D-0F9E-CD40-E0F4-8820645F0A23")   // BLE UUID or MAC
//
//   // Print text with default options
//   printer.printText("Hello World!\nSecond line.")
//
//   // Print with custom options
//   val opts = PrintOptions(
//       depth    = PrintDepth.DARK,
//       type     = PrintType.TEXT,
//       speed    = PrintSpeed.HIGH,
//       copies   = 2,
//       textSize = TextSize.MEDIUM,
//   )
//   printer.printText("Receipt line 1\nReceipt line 2", opts)
//
//   // Print image (Bitmap)
//   val bmp = BitmapFactory.decodeFile("/path/to/image.png")
//   printer.printBitmap(bmp, PrintOptions(depth = PrintDepth.DARK))
//
//   // Print PDF (all pages)
//   printer.printPdf(File("/path/to/document.pdf"))
//
//   // Print only page 0 of PDF, 2 copies
//   printer.printPdf(File("/path/to/document.pdf"), options = PrintOptions(copies = 2), pages = 0..0)
//
//   // Disconnect
//   printer.disconnect()
//
// Required AndroidManifest.xml permissions:
//   <uses-permission android:name="android.permission.BLUETOOTH_SCAN"
//       android:usesPermissionFlags="neverForLocation" />
//   <uses-permission android:name="android.permission.BLUETOOTH_CONNECT" />
//   <!-- For Android 11 and below: -->
//   <uses-permission android:name="android.permission.BLUETOOTH" />
//   <uses-permission android:name="android.permission.BLUETOOTH_ADMIN" />
//   <uses-permission android:name="android.permission.ACCESS_FINE_LOCATION" />
//
// Required build.gradle dependencies:
//   implementation "org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3"
// ─────────────────────────────────────────────────────────────────────────────

// ── Print Option Enums ────────────────────────────────────────────────────────

/**
 * Print darkness / blackening level.
 * Maps to the printer's energy/blackening registers (1–5).
 */
@Suppress("unused")
enum class PrintDepth(val level: Int) {
    @Suppress("unused") VERY_LIGHT(1),
    @Suppress("unused") LIGHT(2),
    NORMAL(3),
    @Suppress("unused") DARK(4),
    @Suppress("unused") VERY_DARK(5),
}

/**
 * Whether to use the printer's text mode, image mode, or let it auto-detect.
 * TEXT mode is faster for plain text; IMAGE mode is better for graphics.
 */
enum class PrintType {
    TEXT,
    IMAGE,
    AUTO,   // Automatically use TEXT when calling printText(), IMAGE for printBitmap/Pdf
}

/**
 * BLE transmission speed profile.
 *
 * | Preset | Chunk | Interval | Print-speed cmd |
 * |--------|-------|----------|-----------------|
 * | NORMAL | 100 B |   20 ms  |       10        |
 * | HIGH   | 100 B |    4 ms  |       20        |
 *
 * Larger chunks or shorter intervals can cause buffer overflow on some
 * printer firmwares — use NORMAL if content is being cut off.
 */
enum class PrintSpeed(val chunkSize: Int, val intervalMs: Long, val printSpeedCmd: Int) {
    NORMAL(chunkSize = 100, intervalMs = 20L, printSpeedCmd = 10),
    HIGH(chunkSize   = 100, intervalMs =  4L, printSpeedCmd = 20),
}

/**
 * Rendered font size when converting text to a bitmap.
 * Larger sizes produce fewer characters per line but are easier to read.
 */
@Suppress("unused")
enum class TextSize(val sp: Float) {
    @Suppress("unused") SMALL(20f),
    MEDIUM(28f),
    @Suppress("unused") LARGE(50f),
    @Suppress("unused") XLARGE(80f),
}

/**
 * Unified print options.
 *
 * @param depth         Darkness level (default: NORMAL = level 3).
 * @param type          Print mode — TEXT, IMAGE, or AUTO (default).
 * @param speed         BLE transmission profile (default: NORMAL).
 * @param copies        Number of copies to print (default: 1).
 * @param textSize      Font size for text rendering (default: MEDIUM = 28 sp).
 * @param pdfPageGapMs  Delay between PDF pages in milliseconds (default: 300).
 */
data class PrintOptions(
    val depth:        PrintDepth = PrintDepth.NORMAL,
    val type:         PrintType  = PrintType.AUTO,
    val speed:        PrintSpeed = PrintSpeed.NORMAL,
    val copies:       Int        = 1,
    val textSize:     TextSize   = TextSize.MEDIUM,
    val pdfPageGapMs: Long       = 300L,
)

// ── Printer model configuration ───────────────────────────────────────────────

data class PrinterModel(
    val printWidth: Int = 384,      // pixels per line (must be multiple of 8)
    val devDpi: Int = 200,
    val energy: Int = 9500,
    val newCompress: Boolean = true,
    val newFormat: Boolean = false,
    val lsbFirst: Boolean = true,   // true for most X-series (a4xii=false)
    val feedPadding: Int = 12,
)

/** Pre-built config for X6h (confirmed working). */
val X6H_MODEL = PrinterModel()

// ── BLE UUIDs (standard ae30 service used by X-series Chinese printers) ───────
private val SERVICE_UUID     = UUID.fromString("0000ae30-0000-1000-8000-00805f9b34fb")
private val WRITE_CHAR_UUID  = UUID.fromString("0000ae01-0000-1000-8000-00805f9b34fb")
private val NOTIFY_CHAR_UUID = UUID.fromString("0000ae02-0000-1000-8000-00805f9b34fb")
private val CCCD_UUID        = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

data class ScannedPrinter(val name: String, val address: String)

// ── Protocol encoder ───────────────────────────────────────────────────────────

private object Protocol {

    /** CRC-8/SMBUS (poly=0x07, init=0x00) */
    fun crc8(data: ByteArray): Byte {
        var crc = 0
        for (b in data) {
            crc = crc xor (b.toInt() and 0xFF)
            repeat(8) {
                crc = if (crc and 0x80 != 0) (crc shl 1) xor 0x07 else crc shl 1
                crc = crc and 0xFF
            }
        }
        return crc.toByte()
    }

    /** Wrap payload in the standard 0x51 0x78 framing. */
    fun makePacket(cmd: Int, payload: ByteArray, newFormat: Boolean): ByteArray {
        val len = payload.size
        val header = byteArrayOf(
            0x51, 0x78,
            (cmd and 0xFF).toByte(),
            0x00,
            (len and 0xFF).toByte(),
            ((len shr 8) and 0xFF).toByte(),
        )
        val checksum = crc8(payload)
        val packet = header + payload + byteArrayOf(checksum, 0xFF.toByte())
        return if (newFormat) byteArrayOf(0x12) + packet else packet
    }

    // ── Command builders ─────────────────────────────────────────────────────

    fun blackeningCmd(level: Int, newFormat: Boolean) =
        makePacket(0xA4, byteArrayOf((0x30 + level.coerceIn(1, 5)).toByte()), newFormat)

    fun energyCmd(energy: Int, newFormat: Boolean): ByteArray {
        if (energy <= 0) return ByteArray(0)
        val payload = byteArrayOf((energy and 0xFF).toByte(), ((energy shr 8) and 0xFF).toByte())
        return makePacket(0xAF, payload, newFormat)
    }

    fun printModeCmd(isText: Boolean, newFormat: Boolean) =
        makePacket(0xBE, byteArrayOf(if (isText) 1 else 0), newFormat)

    fun feedPaperCmd(speed: Int, newFormat: Boolean) =
        makePacket(0xBD, byteArrayOf((speed and 0xFF).toByte()), newFormat)

    fun paperCmd(dpi: Int, newFormat: Boolean): ByteArray {
        val payload = if (dpi == 300) byteArrayOf(0x48, 0x00) else byteArrayOf(0x30, 0x00)
        return makePacket(0xA1, payload, newFormat)
    }

    fun devStateCmd(newFormat: Boolean) =
        makePacket(0xA3, byteArrayOf(0x00), newFormat)

    // ── RLE + line packing ───────────────────────────────────────────────────

    private fun encodeRun(color: Int, count: Int): List<Byte> {
        val out = mutableListOf<Byte>()
        var rem = count
        while (rem > 127) {
            out.add(((color shl 7) or 127).toByte())
            rem -= 127
        }
        if (rem > 0) out.add(((color shl 7) or rem).toByte())
        return out
    }

    fun rleEncodeLine(line: IntArray): ByteArray {
        if (line.isEmpty()) return ByteArray(0)
        val runs = mutableListOf<Byte>()
        var prev = line[0]
        var count = 1
        var hasBlack = prev != 0
        for (i in 1 until line.size) {
            val pix = line[i]
            if (pix != 0) hasBlack = true
            if (pix == prev) count++
            else {
                runs.addAll(encodeRun(prev, count))
                prev = pix; count = 1
            }
        }
        if (hasBlack) runs.addAll(encodeRun(prev, count))
        if (runs.isEmpty()) runs.addAll(encodeRun(prev, count))
        return runs.toByteArray()
    }

    fun packLine(line: IntArray, lsbFirst: Boolean): ByteArray {
        val out = ByteArray(line.size / 8)
        for (i in out.indices) {
            val chunk = line.sliceArray(i * 8 until minOf(i * 8 + 8, line.size))
            var value = 0
            if (lsbFirst) {
                for ((bit, pix) in chunk.withIndex()) if (pix != 0) value = value or (1 shl bit)
            } else {
                for ((bit, pix) in chunk.withIndex()) if (pix != 0) value = value or (1 shl (7 - bit))
            }
            out[i] = value.toByte()
        }
        return out
    }

    // ── Full job builder ─────────────────────────────────────────────────────

    /**
     * Build the complete byte sequence to print a 1-bit raster image.
     *
     * @param pixels    Flat list of 0/1 values, row-major (0=white, 1=black).
     * @param width     Image width in pixels (must be divisible by 8).
     * @param isText    Use text mode (true) or image mode (false).
     * @param model     Printer model configuration.
     * @param options   Print options (depth, speed, etc.).
     */
    fun buildJob(
        pixels: IntArray,
        width: Int,
        isText: Boolean,
        model: PrinterModel,
        options: PrintOptions,
    ): ByteArray {
        require(width % 8 == 0) { "Width must be divisible by 8" }
        val speed      = options.speed.printSpeedCmd
        val blackening = options.depth.level
        val widthBytes = width / 8
        val height     = pixels.size / width
        val nf         = model.newFormat

        val job = mutableListOf<Byte>()
        job.addAll(blackeningCmd(blackening, nf).asList())
        job.addAll(energyCmd(model.energy, nf).asList())
        job.addAll(printModeCmd(isText, nf).asList())
        job.addAll(feedPaperCmd(speed, nf).asList())

        for (row in 0 until height) {
            val line = pixels.sliceArray(row * width until (row + 1) * width)
            val rle  = rleEncodeLine(line)
            if (model.newCompress && rle.size <= widthBytes) {
                job.addAll(makePacket(0xBF, rle, nf).asList())
            } else {
                job.addAll(makePacket(0xA2, packLine(line, model.lsbFirst), nf).asList())
            }
            if ((row + 1) % 200 == 0) job.addAll(feedPaperCmd(speed, nf).asList())
        }

        job.addAll(feedPaperCmd(model.feedPadding, nf).asList())
        job.addAll(paperCmd(model.devDpi, nf).asList())
        job.addAll(paperCmd(model.devDpi, nf).asList())
        job.addAll(feedPaperCmd(model.feedPadding, nf).asList())
        job.addAll(devStateCmd(nf).asList())

        return job.toByteArray()
    }
}

// ── Bitmap → 1-bit pixels ──────────────────────────────────────────────────────

@Suppress("UseKtx")
private object Renderer {

    /**
     * Convert any Bitmap to a 1-bit (black/white) pixel array scaled to
     * [targetWidth]. Uses Floyd-Steinberg dithering.
     */
    fun bitmapToPixels(src: Bitmap, targetWidth: Int): IntArray {
        val w = targetWidth - (targetWidth % 8)
        val h = (src.height.toFloat() * w / src.width).toInt().coerceAtLeast(1)
        val scaled = Bitmap.createScaledBitmap(src, w, h, true)

        val grey = FloatArray(w * h)
        for (y in 0 until h) {
            for (x in 0 until w) {
                val c = scaled.getPixel(x, y)
                val r = (c shr 16) and 0xFF
                val g = (c shr 8) and 0xFF
                val b = c and 0xFF
                grey[y * w + x] = (0.299f * r + 0.587f * g + 0.114f * b) / 255f
            }
        }

        val pixels = IntArray(w * h)
        for (y in 0 until h) {
            for (x in 0 until w) {
                val idx = y * w + x
                val old = grey[idx]
                val bw  = if (old < 0.5f) 1 else 0
                pixels[idx] = bw
                val err = old - (if (bw == 1) 0f else 1f)
                if (x + 1 < w)     grey[idx + 1]     += err * 7f / 16f
                if (y + 1 < h) {
                    if (x > 0)     grey[idx + w - 1] += err * 3f / 16f
                    grey[idx + w]     += err * 5f / 16f
                    if (x + 1 < w) grey[idx + w + 1] += err * 1f / 16f
                }
            }
        }
        return pixels
    }

    /** Render a plain-text string to a Bitmap at the given printer width. */
    fun textToBitmap(text: String, printerWidth: Int, textSizeSp: Float = 28f): Bitmap {
        val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color     = Color.BLACK
            textSize  = textSizeSp
            typeface  = Typeface.MONOSPACE
        }
        val lineHeight = (paint.descent() - paint.ascent()).toInt() + 2
        val lines      = text.split("\n")
        val height     = (lineHeight * lines.size).coerceAtLeast(1)

        val bmp    = Bitmap.createBitmap(printerWidth, height, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bmp)
        canvas.drawColor(Color.WHITE)
        var y = -paint.ascent()
        for (line in lines) {
            canvas.drawText(line, 0f, y, paint)
            y += lineHeight
        }
        return bmp
    }
}

// ── Scan events ───────────────────────────────────────────────────────────────

/** Real-time events emitted during a BLE scan. */
sealed class ScanEvent {
    /** A new device was discovered. */
    data class DeviceFound(val device: ScannedPrinter) : ScanEvent()
    /** The BLE scanner reported an error before finishing. */
    data class ScanFailed(val errorCode: Int, val reason: String) : ScanEvent()
    /** Bluetooth adapter is off or unavailable on this device. */
    object BluetoothUnavailable : ScanEvent()
}

// ── Main printer class ─────────────────────────────────────────────────────────

@SuppressLint("MissingPermission")
class TiMiniPrinter(
    private val context: Context,
    private val model: PrinterModel = X6H_MODEL,
    private val scope: CoroutineScope = CoroutineScope(Dispatchers.IO + SupervisorJob()),
) {
    private val bluetoothManager = context.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager
    private val bluetoothAdapter: BluetoothAdapter? get() = bluetoothManager.adapter

    private var gatt: BluetoothGatt? = null
    private var writeChar: BluetoothGattCharacteristic? = null

    // ── Scan ──────────────────────────────────────────────────────────────────

    /**
     * Scan for nearby BLE printers for [timeoutMs] milliseconds.
     * Calls [onResult] with the complete list of found devices when done.
     *
     * The optional [onEvent] callback fires on the **Main thread** for every
     * real-time event during the scan (new device found, scan failure, BT off),
     * which lets callers log progress as it happens.
     *
     * Safe for all Android OEMs: guards against disabled Bluetooth adapter and
     * null scanner (some Samsung/OPPO devices return null scanner when BT is off).
     */
    @RequiresPermission(allOf = ["android.permission.BLUETOOTH_SCAN", "android.permission.BLUETOOTH_CONNECT"])
    fun scan(
        timeoutMs: Long = 8000,
        onEvent: ((ScanEvent) -> Unit)? = null,
        onResult: (List<ScannedPrinter>) -> Unit,
    ) {
        val adapter = bluetoothAdapter
        if (adapter == null || !adapter.isEnabled) {
            scope.launch(Dispatchers.Main) {
                onEvent?.invoke(ScanEvent.BluetoothUnavailable)
                onResult(emptyList())
            }
            return
        }
        val scanner: BluetoothLeScanner = adapter.bluetoothLeScanner ?: run {
            scope.launch(Dispatchers.Main) {
                onEvent?.invoke(ScanEvent.BluetoothUnavailable)
                onResult(emptyList())
            }
            return
        }
        val found = mutableListOf<ScannedPrinter>()
        val cb = object : ScanCallback() {
            override fun onScanResult(callbackType: Int, result: ScanResult) {
                val name = result.device.name?.takeIf { it.isNotBlank() } ?: return
                val addr = result.device.address ?: return
                if (found.none { it.address == addr }) {
                    val device = ScannedPrinter(name, addr)
                    found.add(device)
                    scope.launch(Dispatchers.Main) {
                        onEvent?.invoke(ScanEvent.DeviceFound(device))
                    }
                }
            }

            override fun onScanFailed(errorCode: Int) {
                val reason = when (errorCode) {
                    SCAN_FAILED_ALREADY_STARTED -> "scan already started"
                    SCAN_FAILED_APPLICATION_REGISTRATION_FAILED -> "app registration failed"
                    SCAN_FAILED_FEATURE_UNSUPPORTED -> "BLE feature unsupported"
                    SCAN_FAILED_INTERNAL_ERROR -> "internal BLE error"
                    else                                              -> "unknown error ($errorCode)"
                }
                scope.launch(Dispatchers.Main) {
                    onEvent?.invoke(ScanEvent.ScanFailed(errorCode, reason))
                    onResult(found)
                }
            }
        }
        scanner.startScan(cb)
        scope.launch {
            delay(timeoutMs)
            try { scanner.stopScan(cb) } catch (_: Exception) {}
            withContext(Dispatchers.Main) { onResult(found) }
        }
    }

    // ── Connect ───────────────────────────────────────────────────────────────

    /**
     * Connect to a printer by BLE MAC address.
     * Suspends until connected and ready to print.
     *
     * Must be called from a coroutine (will switch to Main thread internally as
     * required by the Android BLE stack on Samsung, OPPO, and other OEMs).
     */
    @RequiresPermission("android.permission.BLUETOOTH_CONNECT")
    suspend fun connect(address: String) {
        val adapter = bluetoothAdapter ?: error("Bluetooth not available")
        if (!adapter.isEnabled) error("Bluetooth is disabled")

        val device = withContext(Dispatchers.Main) {
            adapter.getRemoteDevice(address)
        } ?: error("Invalid BLE address: $address")

        val deferred = CompletableDeferred<Unit>()

        val callback = object : BluetoothGattCallback() {
            override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
                when (newState) {
                    BluetoothProfile.STATE_CONNECTED -> g.discoverServices()
                    BluetoothProfile.STATE_DISCONNECTED if !deferred.isCompleted ->
                        deferred.completeExceptionally(
                            Exception("Disconnected before ready (status=$status)")
                        )
                }
            }

            override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
                if (status != BluetoothGatt.GATT_SUCCESS) {
                    deferred.completeExceptionally(Exception("Service discovery failed (status=$status)"))
                    return
                }

                val svc = g.getService(SERVICE_UUID)
                    ?: g.services.firstOrNull()

                val write = svc?.getCharacteristic(WRITE_CHAR_UUID)
                    ?: svc?.characteristics?.firstOrNull {
                        it.properties and BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE != 0
                    }
                    ?: svc?.characteristics?.firstOrNull {
                        it.properties and BluetoothGattCharacteristic.PROPERTY_WRITE != 0
                    }

                if (write == null) {
                    deferred.completeExceptionally(Exception("No writable characteristic found"))
                    return
                }

                writeChar = write
                gatt = g

                // Subscribe to notify characteristic (required by X-series printers)
                val notifyChar = svc?.getCharacteristic(NOTIFY_CHAR_UUID)
                    ?: svc?.characteristics?.firstOrNull {
                        it.properties and BluetoothGattCharacteristic.PROPERTY_NOTIFY != 0
                    }

                if (notifyChar != null) {
                    g.setCharacteristicNotification(notifyChar, true)
                    val descriptor = notifyChar.getDescriptor(CCCD_UUID)
                    if (descriptor != null) {
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                            g.writeDescriptor(descriptor, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE)
                        } else {
                            @Suppress("DEPRECATION")
                            descriptor.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
                            @Suppress("DEPRECATION")
                            g.writeDescriptor(descriptor)
                        }
                    }
                }

                deferred.complete(Unit)
            }

            override fun onCharacteristicWrite(
                g: BluetoothGatt, characteristic: BluetoothGattCharacteristic, status: Int
            ) { /* no-op: write-without-response does not trigger this */ }
        }

        // connectGatt MUST be called from the Main thread on Samsung, OPPO, Huawei etc.
        withContext(Dispatchers.Main) {
            gatt = device.connectGatt(context, false, callback, BluetoothDevice.TRANSPORT_LE)
        }

        withTimeout(15_000) { deferred.await() }

        // Allow 600 ms for the Samsung/MediaTek GATT stack to fully stabilize
        // before the first write. Without this, the first packet is silently dropped.
        delay(600)
    }

    // ── Print: Text ───────────────────────────────────────────────────────────

    /**
     * Print a plain-text string rendered with monospace font.
     *
     * @param text     The string to print (\n for line breaks).
     * @param options  Print settings: depth, speed, copies, text size, etc.
     */
    suspend fun printText(text: String, options: PrintOptions = PrintOptions()) {
        val isText = options.type != PrintType.IMAGE   // TEXT or AUTO → text mode
        val bmp    = Renderer.textToBitmap(text, model.printWidth, options.textSize.sp)
        val pixels = Renderer.bitmapToPixels(bmp, model.printWidth)
        val job    = Protocol.buildJob(pixels, model.printWidth, isText, model, options)
        repeat(options.copies.coerceAtLeast(1)) { idx ->
            if (idx > 0) delay(300)
            sendRaw(job, options.speed)
        }
    }

    // ── Print: Bitmap / Image ─────────────────────────────────────────────────

    /**
     * Print a [Bitmap]. Pass any bitmap loaded from file, camera, resource, etc.
     *
     * @param bitmap   Source bitmap (any size; will be scaled to printer width).
     * @param options  Print settings.
     */
    suspend fun printBitmap(bitmap: Bitmap, options: PrintOptions = PrintOptions()) {
        val isText = options.type == PrintType.TEXT
        val pixels = Renderer.bitmapToPixels(bitmap, model.printWidth)
        val job    = Protocol.buildJob(pixels, model.printWidth, isText, model, options)
        repeat(options.copies.coerceAtLeast(1)) { idx ->
            if (idx > 0) delay(300)
            sendRaw(job, options.speed)
        }
    }

    // ── Print: PDF ────────────────────────────────────────────────────────────

    /**
     * Print pages of a PDF file.
     *
     * @param file     PDF file on device storage.
     * @param options  Print settings (copies = per full document).
     * @param pages    Range of 0-based page indices to print (default: all pages).
     */
    suspend fun printPdf(file: File, options: PrintOptions = PrintOptions(), pages: IntRange? = null) {
        val pfd      = ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY)
        val renderer = PdfRenderer(pfd)
        val pageRange = pages ?: (0 until renderer.pageCount)

        suspend fun renderAndPrintAllPages() {
            try {
                for (pageIdx in pageRange) {
                    if (pageIdx >= renderer.pageCount) break
                    val page  = renderer.openPage(pageIdx)
                    val scale = model.printWidth.toFloat() / page.width
                    val bmpH  = (page.height * scale).toInt().coerceAtLeast(1)
                    @Suppress("UseKtx")
                    val bmp   = Bitmap.createBitmap(model.printWidth, bmpH, Bitmap.Config.ARGB_8888)
                    bmp.eraseColor(Color.WHITE)
                    page.render(bmp, null, null, PdfRenderer.Page.RENDER_MODE_FOR_PRINT)
                    page.close()

                    val isText = options.type == PrintType.TEXT
                    val pixels = Renderer.bitmapToPixels(bmp, model.printWidth)
                    val job    = Protocol.buildJob(pixels, model.printWidth, isText, model, options)
                    sendRaw(job, options.speed)

                    val isLastPage = pageIdx == (pages?.last ?: (renderer.pageCount - 1))
                    if (!isLastPage) delay(options.pdfPageGapMs)
                }
            } finally {
                renderer.close()
                pfd.close()
            }
        }

        repeat(options.copies.coerceAtLeast(1)) { idx ->
            if (idx > 0) delay(300)
            renderAndPrintAllPages()
        }
    }

    // ── Feed paper ────────────────────────────────────────────────────────────

    @Suppress("unused")
    suspend fun feedPaper() {
        sendRaw(Protocol.paperCmd(model.devDpi, model.newFormat), PrintSpeed.NORMAL)
    }

    // ── Disconnect ────────────────────────────────────────────────────────────

    /**
     * Gracefully disconnect from the printer.
     * Safe to call from any thread. Internally dispatches to Main thread as
     * required by Samsung, OPPO, and Huawei BLE stacks.
     */
    @SuppressLint("MissingPermission")
    fun disconnect() {
        val currentGatt = gatt
        gatt = null
        writeChar = null
        if (currentGatt != null) {
            scope.launch(Dispatchers.Main) {
                try {
                    currentGatt.disconnect()
                    // Short delay before close() prevents a known GATT resource leak
                    // on Samsung / Huawei that can crash the BLE stack if close() is
                    // called immediately after disconnect().
                    delay(200)
                } catch (_: Exception) {
                } finally {
                    try { currentGatt.close() } catch (_: Exception) {}
                }
            }
        }
    }

    // ── Internal BLE write ────────────────────────────────────────────────────

    /**
     * Send raw bytes to the printer using the given [speed] profile.
     * Data is split into [PrintSpeed.chunkSize] chunks separated by [PrintSpeed.intervalMs].
     */
    private suspend fun sendRaw(data: ByteArray, speed: PrintSpeed) {
        val g    = gatt      ?: error("Not connected to printer")
        val char = writeChar ?: error("No write characteristic found")

        for (offset in data.indices step speed.chunkSize) {
            val chunk = data.copyOfRange(offset, minOf(offset + speed.chunkSize, data.size))
            withContext(Dispatchers.IO) { writeChunk(g, char, chunk) }
            if (speed.intervalMs > 0L) delay(speed.intervalMs)
        }
    }

    @RequiresPermission("android.permission.BLUETOOTH_CONNECT")
    private fun writeChunk(
        g: BluetoothGatt,
        char: BluetoothGattCharacteristic,
        chunk: ByteArray,
    ) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            g.writeCharacteristic(char, chunk, BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE)
        } else {
            @Suppress("DEPRECATION")
            char.writeType = BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE
            @Suppress("DEPRECATION")
            char.value = chunk
            @Suppress("DEPRECATION")
            g.writeCharacteristic(char)
        }
    }
}
