// TiMiniPrinter.swift
// iOS / Swift port of the Android TiMiniPrinter library.
// Requires: iOS 14+  |  Swift 5.9+
//
// ─────────────────────────────────────────────────────────────────────────────
// Quick-start:
//
//   let printer = TiMiniPrinter()
//
//   // 1. Scan (devices use CoreBluetooth UUID strings, not MAC addresses)
//   printer.scan(timeoutSeconds: 8, onEvent: { event in
//       if case .deviceFound(let d) = event { print("Found: \(d.name)") }
//   }, onResult: { devices in
//       // store devices[n].address (UUID string) for connect()
//   })
//
//   // 2. Connect
//   Task {
//       try await printer.connect(address: "F464B34D-0F9E-CD40-E0F4-8820645F0A23")
//
//       // 3. Print
//       try await printer.printText("Hello World!\nSecond line.")
//       try await printer.printText("Hello World!\nSecond line.") { percent in
//           print("Progress: \(percent)%")
//       }
//
//       let opts = PrintOptions(depth: .dark, speed: .high, copies: 2, textSize: .medium)
//       try await printer.printText("Receipt line 1\nReceipt line 2", options: opts)
//
//       let img = UIImage(named: "logo")!
//       try await printer.printImage(img, options: PrintOptions(depth: .dark))
//
//       try await printer.printPDF(url: fileURL)
//       try await printer.printPDF(url: fileURL, options: PrintOptions(copies: 2), pages: 0...0)
//
//       // 4. Disconnect
//       printer.disconnect()
//   }
//
// Required Info.plist keys:
//   <key>NSBluetoothAlwaysUsageDescription</key>
//   <string>Used to connect to the mini thermal printer.</string>
//
// Required frameworks (all system, no SPM needed):
//   CoreBluetooth, UIKit, PDFKit, CoreGraphics
// ─────────────────────────────────────────────────────────────────────────────

import CoreBluetooth
import CoreGraphics
import Foundation
import PDFKit
import UIKit

// MARK: - Print Option Enums

/// Print darkness level — maps to the printer's blackening register (1–5).
public enum PrintDepth: Int {
    case veryLight = 1
    case light     = 2
    case normal    = 3
    case dark      = 4
    case veryDark  = 5
}

/// Whether to use the printer's text mode, image mode, or auto-detect.
public enum PrintType {
    /// Force text mode (faster, lighter weight).
    case text
    /// Force image mode (better for graphics/photos).
    case image
    /// Auto: text mode for `printText()`, image mode for `printImage()`/`printPDF()`.
    case auto
}

/// BLE transmission speed profile.
///
/// | Preset | Chunk | Interval | Speed cmd |
/// |--------|-------|----------|-----------|
/// | normal | 100 B |   20 ms  |     10    |
/// | high   | 100 B |    4 ms  |     20    |
///
/// Use `.normal` if content is being cut off (buffer overflow at high speed).
public enum PrintSpeed {
    case normal
    case high

    /// Number of bytes per BLE write.
    public var chunkSize: Int     { 100 }
    /// Delay between chunks in milliseconds.
    public var intervalMs: UInt64 { self == .high ? 4 : 20 }
    /// Value sent in the feed-paper speed command.
    public var printSpeedCmd: Int { self == .high ? 20 : 10 }
}

/// Font size used when rendering text to a bitmap.
public enum TextSize {
    case small
    case medium
    case large
    case xlarge

    /// Point size passed to UIFont.
    public var points: CGFloat {
        switch self {
        case .small:  return 20
        case .medium: return 28
        case .large:  return 50
        case .xlarge: return 80
        }
    }
}

/// Unified print options passed to every print method.
public struct PrintOptions {
    /// Darkness level. Default: `.normal`.
    public var depth: PrintDepth
    /// Print mode. Default: `.auto`.
    public var type: PrintType
    /// BLE speed profile. Default: `.normal`.
    public var speed: PrintSpeed
    /// Number of copies. Default: `1`.
    public var copies: Int
    /// Font size for text rendering. Default: `.medium`.
    public var textSize: TextSize
    /// Milliseconds to wait between PDF pages. Default: `300`.
    public var pdfPageGapMs: UInt64

    public init(
        depth:        PrintDepth = .normal,
        type:         PrintType  = .auto,
        speed:        PrintSpeed = .normal,
        copies:       Int        = 1,
        textSize:     TextSize   = .medium,
        pdfPageGapMs: UInt64     = 300
    ) {
        self.depth        = depth
        self.type         = type
        self.speed        = speed
        self.copies       = copies
        self.textSize     = textSize
        self.pdfPageGapMs = pdfPageGapMs
    }
}

// MARK: - Printer Model Configuration

/// Hardware parameters for a specific printer model.
public struct PrinterModel {
    /// Pixels per line — must be a multiple of 8. Default: 384.
    public var printWidth: Int
    /// Native DPI. Default: 200.
    public var devDpi: Int
    /// Thermal energy register value. Default: 9500 (verified on X6h).
    public var energy: Int
    /// Use RLE packet (0xBF) when the run encodes shorter than raw. Default: true.
    public var newCompress: Bool
    /// Prefix each packet with 0x12 (new-format firmware). Default: false.
    public var newFormat: Bool
    /// Pack bits LSB-first. Default: true (all X-series). Set false for a4xii.
    public var lsbFirst: Bool
    /// Feed-paper lines appended before/after content. Default: 12.
    public var feedPadding: Int

    public init(
        printWidth:  Int  = 384,
        devDpi:      Int  = 200,
        energy:      Int  = 9500,
        newCompress: Bool = true,
        newFormat:   Bool = false,
        lsbFirst:    Bool = true,
        feedPadding: Int  = 12
    ) {
        self.printWidth  = printWidth
        self.devDpi      = devDpi
        self.energy      = energy
        self.newCompress = newCompress
        self.newFormat   = newFormat
        self.lsbFirst    = lsbFirst
        self.feedPadding = feedPadding
    }
}

extension PrinterModel {
    /// Pre-built config for X6h (confirmed working).
    /// Stored constant so it is `nonisolated` and safe from any context.
    public static let x6h: PrinterModel = PrinterModel()
}

/// Convenience alias — same as `PrinterModel.x6h`.
public var x6hModel: PrinterModel { .x6h }

// MARK: - BLE UUIDs

private let kServiceUUID    = CBUUID(string: "0000AE30-0000-1000-8000-00805F9B34FB")
private let kWriteCharUUID  = CBUUID(string: "0000AE01-0000-1000-8000-00805F9B34FB")
private let kNotifyCharUUID = CBUUID(string: "0000AE02-0000-1000-8000-00805F9B34FB")

// MARK: - Public Types

/// A printer discovered during a BLE scan.
public struct ScannedPrinter: Equatable, Sendable {
    /// Peripheral display name (e.g. "X6h-B98D").
    public let name: String
    /// CoreBluetooth UUID string — pass this to `connect(address:)`.
    public let address: String
}

/// Real-time events emitted during a BLE scan.
public enum ScanEvent: Sendable {
    /// A new printer was discovered.
    case deviceFound(ScannedPrinter)
    /// The BLE scanner reported an error before finishing.
    case scanFailed(code: Int, reason: String)
    /// Bluetooth is off or unavailable on this device.
    case bluetoothUnavailable
}

// MARK: - Errors

public enum TiMiniPrinterError: Error, LocalizedError {
    case bluetoothUnavailable
    case bluetoothOff
    case connectionTimeout
    case connectionFailed(String)
    case notConnected
    case serviceNotFound
    case characteristicNotFound
    case pdfLoadFailed(URL)

    public var errorDescription: String? {
        switch self {
        case .bluetoothUnavailable:
            return "Bluetooth is not available on this device."
        case .bluetoothOff:
            return "Bluetooth is turned off."
        case .connectionTimeout:
            return "Connection timed out after 15 seconds."
        case .connectionFailed(let msg):
            return "Connection failed: \(msg)"
        case .notConnected:
            return "Printer is not connected. Call connect() first."
        case .serviceNotFound:
            return "AE30 service not found. Is this a supported printer?"
        case .characteristicNotFound:
            return "No writable characteristic found on AE30 service."
        case .pdfLoadFailed(let url):
            return "Failed to load PDF at \(url.lastPathComponent)."
        }
    }
}

// MARK: - Protocol Encoder

// All methods are pure / stateless — collected in a caseless enum as a namespace.
private enum PrinterProtocol {

    // ── CRC-8/SMBUS (poly = 0x07, init = 0x00) ──────────────────────────────

    static func crc8(_ data: [UInt8]) -> UInt8 {
        var crc: UInt8 = 0
        for byte in data {
            crc ^= byte
            for _ in 0..<8 {
                crc = (crc & 0x80) != 0 ? (crc << 1) ^ 0x07 : crc << 1
            }
        }
        return crc
    }

    // ── Packet framing ───────────────────────────────────────────────────────

    static func makePacket(cmd: UInt8, payload: [UInt8], newFormat: Bool) -> [UInt8] {
        let len = payload.count
        let header: [UInt8] = [
            0x51, 0x78,
            cmd,
            0x00,
            UInt8(len & 0xFF),
            UInt8((len >> 8) & 0xFF),
        ]
        let checksum = crc8(payload)
        let packet: [UInt8] = header + payload + [checksum, 0xFF]
        return newFormat ? [0x12] + packet : packet
    }

    // ── Command builders ─────────────────────────────────────────────────────

    static func blackeningCmd(level: Int, newFormat: Bool) -> [UInt8] {
        let clamped = max(1, min(5, level))
        return makePacket(cmd: 0xA4, payload: [UInt8(0x30 + clamped)], newFormat: newFormat)
    }

    static func energyCmd(energy: Int, newFormat: Bool) -> [UInt8] {
        guard energy > 0 else { return [] }
        let payload: [UInt8] = [UInt8(energy & 0xFF), UInt8((energy >> 8) & 0xFF)]
        return makePacket(cmd: 0xAF, payload: payload, newFormat: newFormat)
    }

    static func printModeCmd(isText: Bool, newFormat: Bool) -> [UInt8] {
        makePacket(cmd: 0xBE, payload: [isText ? 1 : 0], newFormat: newFormat)
    }

    static func feedPaperCmd(speed: Int, newFormat: Bool) -> [UInt8] {
        makePacket(cmd: 0xBD, payload: [UInt8(speed & 0xFF)], newFormat: newFormat)
    }

    static func paperCmd(dpi: Int, newFormat: Bool) -> [UInt8] {
        let payload: [UInt8] = dpi == 300 ? [0x48, 0x00] : [0x30, 0x00]
        return makePacket(cmd: 0xA1, payload: payload, newFormat: newFormat)
    }

    static func devStateCmd(newFormat: Bool) -> [UInt8] {
        makePacket(cmd: 0xA3, payload: [0x00], newFormat: newFormat)
    }

    // ── RLE encoding ─────────────────────────────────────────────────────────

    private static func encodeRun(color: Int, count: Int) -> [UInt8] {
        var result: [UInt8] = []
        var remaining = count
        while remaining > 127 {
            result.append(UInt8((color << 7) | 127))
            remaining -= 127
        }
        if remaining > 0 {
            result.append(UInt8((color << 7) | remaining))
        }
        return result
    }

    static func rleEncodeLine(_ line: [Int]) -> [UInt8] {
        guard !line.isEmpty else { return [] }
        var runs: [UInt8] = []
        var prev  = line[0]
        var count = 1
        var hasBlack = prev != 0

        for i in 1..<line.count {
            let pix = line[i]
            if pix != 0 { hasBlack = true }
            if pix == prev {
                count += 1
            } else {
                runs += encodeRun(color: prev, count: count)
                prev  = pix
                count = 1
            }
        }
        // Always flush the last run; omit empty (all-white) lines only when truly blank
        runs += encodeRun(color: prev, count: count)
        if !hasBlack { runs.removeAll() }
        if runs.isEmpty { runs += encodeRun(color: 0, count: line.count) }
        return runs
    }

    // ── Bit packing ───────────────────────────────────────────────────────────

    static func packLine(_ line: [Int], lsbFirst: Bool) -> [UInt8] {
        let byteCount = line.count / 8
        var out = [UInt8](repeating: 0, count: byteCount)
        for i in 0..<byteCount {
            let start = i * 8
            let end   = min(start + 8, line.count)
            var value: UInt8 = 0
            for bit in 0..<(end - start) {
                if line[start + bit] != 0 {
                    value |= lsbFirst ? (1 << bit) : (1 << (7 - bit))
                }
            }
            out[i] = value
        }
        return out
    }

    // ── Job builder ───────────────────────────────────────────────────────────

    /// Build the complete byte sequence to print a 1-bit raster image.
    ///
    /// - Parameters:
    ///   - pixels:  Flat array — 0 = white, 1 = black, row-major.
    ///   - width:   Image width in pixels (divisible by 8).
    ///   - isText:  Send text-mode command (true) or image-mode (false).
    ///   - model:   Printer hardware config.
    ///   - options: User-facing print options.
    /// - Returns: Raw bytes ready to be sent over BLE.
    static func buildJob(
        pixels: [Int],
        width: Int,
        isText: Bool,
        model: PrinterModel,
        options: PrintOptions
    ) -> Data {
        precondition(width % 8 == 0, "Width must be divisible by 8")
        let speed      = options.speed.printSpeedCmd
        let blackening = options.depth.rawValue
        let widthBytes = width / 8
        let height     = pixels.count / width
        let nf         = model.newFormat

        var job: [UInt8] = []
        job += blackeningCmd(level: blackening, newFormat: nf)
        job += energyCmd(energy: model.energy, newFormat: nf)
        job += printModeCmd(isText: isText, newFormat: nf)
        job += feedPaperCmd(speed: speed, newFormat: nf)

        for row in 0..<height {
            let line = Array(pixels[(row * width)..<((row + 1) * width)])
            let rle  = rleEncodeLine(line)
            if model.newCompress && rle.count <= widthBytes {
                job += makePacket(cmd: 0xBF, payload: rle, newFormat: nf)
            } else {
                job += makePacket(cmd: 0xA2, payload: packLine(line, lsbFirst: model.lsbFirst), newFormat: nf)
            }
            if (row + 1) % 200 == 0 {
                job += feedPaperCmd(speed: speed, newFormat: nf)
            }
        }

        job += feedPaperCmd(speed: model.feedPadding, newFormat: nf)
        job += paperCmd(dpi: model.devDpi, newFormat: nf)
        job += paperCmd(dpi: model.devDpi, newFormat: nf)
        job += feedPaperCmd(speed: model.feedPadding, newFormat: nf)
        job += devStateCmd(newFormat: nf)

        return Data(job)
    }
}

// MARK: - Renderer

private enum PrinterRenderer {

    /// Re-draw `image` so its CGImage has orientation = .up.
    /// UIImage from the photo library carries orientation as metadata; CGImage itself
    /// has no orientation. Drawing via UIGraphicsImageRenderer bakes the transform in,
    /// preventing portrait photos from rendering with swapped width/height.
    private static func normalized(_ image: UIImage) -> UIImage {
        guard image.imageOrientation != .up else { return image }
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        return UIGraphicsImageRenderer(size: image.size, format: format).image { _ in
            image.draw(at: .zero)
        }
    }

    /// Convert a UIImage to a 1-bit pixel array (0 = white, 1 = black) scaled
    /// to `targetWidth` px using Floyd-Steinberg dithering.
    static func imageToPixels(_ src: UIImage, targetWidth: Int) -> [Int] {
        let src = normalized(src)   // bake in EXIF orientation before reading cgImage
        let w = targetWidth - (targetWidth % 8)
        guard w > 0, let srcCG = src.cgImage else { return [] }

        let origW = srcCG.width
        let origH = srcCG.height
        guard origW > 0, origH > 0 else { return [] }
        let h = max(1, Int(Double(origH) * Double(w) / Double(origW)))

        // Render into an 8-bit greyscale bitmap
        let colorSpace = CGColorSpaceCreateDeviceGray()
        guard let ctx = CGContext(
            data: nil,
            width: w,
            height: h,
            bitsPerComponent: 8,
            bytesPerRow: w,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.none.rawValue
        ) else { return [] }

        ctx.setFillColor(gray: 1.0, alpha: 1.0)
        ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
        ctx.draw(srcCG, in: CGRect(x: 0, y: 0, width: w, height: h))

        guard let rawData = ctx.data else { return [] }
        let buf = rawData.bindMemory(to: UInt8.self, capacity: w * h)

        // Build float grey array
        var grey = [Float](repeating: 0, count: w * h)
        for i in 0..<(w * h) {
            grey[i] = Float(buf[i]) / 255.0
        }

        // Floyd-Steinberg dithering
        var pixels = [Int](repeating: 0, count: w * h)
        for y in 0..<h {
            for x in 0..<w {
                let idx = y * w + x
                let old = grey[idx]
                let bw  = old < 0.5 ? 1 : 0   // 1 = black, 0 = white
                pixels[idx] = bw
                let err = old - (bw == 1 ? 0.0 : 1.0)
                if x + 1 < w {
                    grey[idx + 1] += err * 7.0 / 16.0
                }
                if y + 1 < h {
                    if x > 0 {
                        grey[idx + w - 1] += err * 3.0 / 16.0
                    }
                    grey[idx + w] += err * 5.0 / 16.0
                    if x + 1 < w {
                        grey[idx + w + 1] += err * 1.0 / 16.0
                    }
                }
            }
        }
        return pixels
    }

    /// Render a plain-text string to a UIImage at `printerWidth` px width.
    static func textToImage(_ text: String, printerWidth: Int, fontSize: CGFloat) -> UIImage {
        let font = UIFont(name: "Courier", size: fontSize) ?? UIFont.systemFont(ofSize: fontSize)
        let attributes: [NSAttributedString.Key: Any] = [
            .font:            font,
            .foregroundColor: UIColor.black,
        ]
        let lines      = text.components(separatedBy: "\n")
        let lineHeight = font.lineHeight + 2.0
        let totalH     = max(1, CGFloat(lines.count) * lineHeight)
        let renderSize = CGSize(width: CGFloat(printerWidth), height: totalH)

        let rendererFormat = UIGraphicsImageRendererFormat()
        rendererFormat.scale = 1.0   // pixel-exact; no Retina scaling
        let renderer = UIGraphicsImageRenderer(size: renderSize, format: rendererFormat)
        return renderer.image { _ in
            UIColor.white.setFill()
            UIRectFill(CGRect(origin: .zero, size: renderSize))
            for (i, line) in lines.enumerated() {
                let point = CGPoint(x: 0, y: CGFloat(i) * lineHeight)
                (line as NSString).draw(at: point, withAttributes: attributes)
            }
        }
    }
}

// MARK: - TiMiniPrinter

/// iOS BLE driver for X-series thermal mini printers (X6h, X6D, etc.).
///
/// - Important: Must be created and used on the **Main actor** because
///   `CBCentralManager` requires its delegate callbacks on the main thread.
@MainActor
public final class TiMiniPrinter: NSObject {

    // ── Public configuration ──────────────────────────────────────────────────

    /// Printer hardware parameters used for all print jobs.
    public let model: PrinterModel

    // ── CoreBluetooth ─────────────────────────────────────────────────────────

    private var central: CBCentralManager!
    private var peripheral: CBPeripheral?
    private var writeChar: CBCharacteristic?
    private var notifyChar: CBCharacteristic?

    // ── Pending async continuations ───────────────────────────────────────────

    private var connectContinuation: CheckedContinuation<Void, Error>?
    /// Resumed by `peripheralIsReady` when the BLE transmit queue drains.
    private var writeContinuation:   CheckedContinuation<Void, Never>?

    // ── Scan state ────────────────────────────────────────────────────────────

    private var scanResults:   [ScannedPrinter]          = []
    private var scanOnEvent:   ((ScanEvent) -> Void)?
    private var scanOnResult:  (([ScannedPrinter]) -> Void)?
    private var scanTimer:     Timer?

    // ── Init ──────────────────────────────────────────────────────────────────

    public init(model: PrinterModel) {
        self.model = model
        super.init()
        self.central = CBCentralManager(delegate: self, queue: .main)
    }

    public convenience override init() {
        self.init(model: .x6h)
    }

    // MARK: - Scan

    /// Scan for nearby BLE printers for `timeoutSeconds` seconds.
    ///
    /// The `onEvent` callback (optional) fires on the **main thread** for each
    /// newly discovered device, allowing real-time UI updates.
    /// `onResult` is called on the **main thread** when the timeout expires.
    ///
    /// - Note: On iOS, device identifiers are **UUID strings** (not MAC addresses).
    ///   Store the `address` field from `ScannedPrinter` and pass it to `connect(address:)`.
    public func scan(
        timeoutSeconds: TimeInterval = 8,
        onEvent: ((ScanEvent) -> Void)? = nil,
        onResult: @escaping ([ScannedPrinter]) -> Void
    ) {
        guard central.state == .poweredOn else {
            onEvent?(.bluetoothUnavailable)
            onResult([])
            return
        }

        // Stop any in-progress scan
        central.stopScan()
        scanTimer?.invalidate()

        scanResults   = []
        scanOnEvent   = onEvent
        scanOnResult  = onResult

        // Scan ALL peripherals — many printers (including X6h) do NOT advertise
        // the AE30 service UUID in their advertisement packet; they only expose it
        // after connection. Filtering by service UUID here would miss them.
        central.scanForPeripherals(withServices: nil, options: [
            CBCentralManagerScanOptionAllowDuplicatesKey: false,
        ])

        scanTimer = Timer.scheduledTimer(withTimeInterval: timeoutSeconds, repeats: false) { [weak self] _ in
            // Timer callbacks are not @MainActor — hop explicitly.
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.central.stopScan()
                let results = self.scanResults
                self.scanOnResult?(results)
                self.scanOnEvent  = nil
                self.scanOnResult = nil
            }
        }
    }

    // MARK: - Connect

    /// Connect to a printer by its CoreBluetooth UUID string.
    ///
    /// The UUID is the `address` field returned by `scan()`. Suspends until
    /// the connection is established and GATT characteristics are discovered,
    /// or throws after a 15-second timeout.
    ///
    /// - Parameter address: UUID string (e.g. `"F464B34D-0F9E-CD40-E0F4-8820645F0A23"`).
    public func connect(address: String) async throws {
        guard central.state == .poweredOn else {
            throw TiMiniPrinterError.bluetoothOff
        }
        guard let uuid = UUID(uuidString: address) else {
            throw TiMiniPrinterError.connectionFailed("Invalid UUID string: \(address)")
        }

        // CoreBluetooth can reconnect to any peripheral it has previously seen,
        // even across sessions, via retrievePeripherals(withIdentifiers:).
        let known = central.retrievePeripherals(withIdentifiers: [uuid])
        guard let target = known.first else {
            throw TiMiniPrinterError.connectionFailed(
                "Peripheral '\(address)' not found. Scan first to let CoreBluetooth discover it."
            )
        }

        target.delegate = self
        peripheral = target

        try await withThrowingTaskGroup(of: Void.self) { group in
            // Run the connection on the MainActor so we can set the continuation safely.
            group.addTask { @MainActor in
                try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
                    self.connectContinuation = cont
                    self.central.connect(target, options: nil)
                }
            }

            group.addTask {
                try await Task.sleep(nanoseconds: 15_000_000_000)
                throw TiMiniPrinterError.connectionTimeout
            }

            // Wait for whichever task finishes first (connected or timed out).
            _ = try await group.next()
            group.cancelAll()
        }

        // 600 ms stabilisation delay — prevents the first BLE write from being
        // silently dropped on MediaTek / Samsung GATT stacks.
        try await Task.sleep(nanoseconds: 600_000_000)
    }

    // MARK: - Print: Text

    /// Print a plain-text string rendered in monospace font.
    ///
    /// - Parameters:
    ///   - text:    The string to print — use `\n` for line breaks.
    ///   - options: Print settings (depth, speed, copies, text size, …).
    ///   - onProgressPercent: Optional callback with overall progress `0...100`.
    public func printText(
        _ text: String,
        options: PrintOptions? = nil,
        onProgressPercent: ((Int) -> Void)? = nil
    ) async throws {
        let options = options ?? PrintOptions()
        let isText = options.type != .image
        let img    = PrinterRenderer.textToImage(text, printerWidth: model.printWidth, fontSize: options.textSize.points)
        let pixels = PrinterRenderer.imageToPixels(img, targetWidth: model.printWidth)
        let job    = PrinterProtocol.buildJob(pixels: pixels, width: model.printWidth, isText: isText, model: model, options: options)
        let count  = max(1, options.copies)
        emitProgress(onProgressPercent, 0)
        for idx in 0..<count {
            if idx > 0 { try await Task.sleep(nanoseconds: 300_000_000) }
            try await sendRaw(job, speed: options.speed) { copyPercent in
                let overall = ((idx * 100) + copyPercent) / count
                self.emitProgress(onProgressPercent, overall)
            }
        }
        emitProgress(onProgressPercent, 100)
    }

    // MARK: - Print: Image

    /// Print a `UIImage` — scaled to printer width and dithered to 1-bit.
    ///
    /// - Parameters:
    ///   - image:   Source image (any size / format).
    ///   - options: Print settings.
    ///   - onProgressPercent: Optional callback with overall progress `0...100`.
    public func printImage(
        _ image: UIImage,
        options: PrintOptions? = nil,
        onProgressPercent: ((Int) -> Void)? = nil
    ) async throws {
        let options = options ?? PrintOptions()
        let isText = options.type == .text
        let pixels = PrinterRenderer.imageToPixels(image, targetWidth: model.printWidth)
        let job    = PrinterProtocol.buildJob(pixels: pixels, width: model.printWidth, isText: isText, model: model, options: options)
        let count  = max(1, options.copies)
        emitProgress(onProgressPercent, 0)
        for idx in 0..<count {
            if idx > 0 { try await Task.sleep(nanoseconds: 300_000_000) }
            try await sendRaw(job, speed: options.speed) { copyPercent in
                let overall = ((idx * 100) + copyPercent) / count
                self.emitProgress(onProgressPercent, overall)
            }
        }
        emitProgress(onProgressPercent, 100)
    }

    // MARK: - Print: PDF

    /// Print pages from a PDF file.
    ///
    /// - Parameters:
    ///   - url:     File URL of the PDF on device storage.
    ///   - options: Print settings (`copies` applies per full document run).
    ///   - pages:   0-based closed range of pages to print. Defaults to all pages.
    ///   - onProgressPercent: Optional callback with overall progress `0...100`.
    public func printPDF(
        url: URL,
        options: PrintOptions? = nil,
        pages: ClosedRange<Int>? = nil,
        onProgressPercent: ((Int) -> Void)? = nil
    ) async throws {
        let options = options ?? PrintOptions()
        guard let doc = PDFDocument(url: url) else {
            throw TiMiniPrinterError.pdfLoadFailed(url)
        }
        let pageCount = doc.pageCount
        guard pageCount > 0 else { return }
        let range = pages ?? (0...(pageCount - 1))
        let printablePages = range.filter { $0 >= 0 && $0 < pageCount }
        let isText = options.type == .text

        let count = max(1, options.copies)
        let totalUnits = max(1, printablePages.count * count)
        emitProgress(onProgressPercent, 0)
        for copyIdx in 0..<count {
            if copyIdx > 0 { try await Task.sleep(nanoseconds: 300_000_000) }

            for (pagePos, pageIdx) in printablePages.enumerated() {
                guard let page = doc.page(at: pageIdx) else { continue }

                let pageBounds = page.bounds(for: .mediaBox)
                guard pageBounds.width > 0, pageBounds.height > 0 else { continue }
                let scale      = CGFloat(model.printWidth) / pageBounds.width
                let renderH    = max(1, Int(pageBounds.height * scale))

                let renderSize = CGSize(width: CGFloat(model.printWidth), height: CGFloat(renderH))
                let rendererFormat = UIGraphicsImageRendererFormat()
                rendererFormat.scale = 1.0
                let imgRenderer = UIGraphicsImageRenderer(size: renderSize, format: rendererFormat)

                let bmp = imgRenderer.image { ctx in
                    UIColor.white.setFill()
                    UIRectFill(CGRect(origin: .zero, size: renderSize))
                    let cgCtx = ctx.cgContext
                    cgCtx.translateBy(x: 0, y: renderSize.height)
                    cgCtx.scaleBy(x: scale, y: -scale)
                    page.draw(with: .mediaBox, to: cgCtx)
                }

                let pixels = PrinterRenderer.imageToPixels(bmp, targetWidth: model.printWidth)
                let job    = PrinterProtocol.buildJob(pixels: pixels, width: model.printWidth, isText: isText, model: model, options: options)
                try await sendRaw(job, speed: options.speed) { pagePercent in
                    let doneUnits = (copyIdx * printablePages.count) + pagePos
                    let overall = ((doneUnits * 100) + pagePercent) / totalUnits
                    self.emitProgress(onProgressPercent, overall)
                }

                let isLastPage = pagePos == (printablePages.count - 1)
                if !isLastPage {
                    try await Task.sleep(nanoseconds: options.pdfPageGapMs * 1_000_000)
                }
            }
        }
        emitProgress(onProgressPercent, 100)
    }

    // MARK: - Feed Paper

    /// Advance the paper by one page-feed.
    public func feedPaper() async throws {
        let cmd = PrinterProtocol.paperCmd(dpi: model.devDpi, newFormat: model.newFormat)
        try await sendRaw(Data(cmd), speed: .normal)
    }

    // MARK: - Disconnect

    /// Gracefully disconnect from the printer.
    ///
    /// Safe to call from any context. After this returns the printer instance
    /// can be reconnected via `connect(address:)`.
    public func disconnect() {
        if let p = peripheral {
            central.cancelPeripheralConnection(p)
        }
        peripheral  = nil
        writeChar   = nil
        notifyChar  = nil
    }

    // MARK: - Internal BLE write

    /// Split `data` into chunks and write each chunk with `writeWithoutResponse`.
    ///
    /// Uses `canSendWriteWithoutResponse` to avoid overflowing the CoreBluetooth
    /// transmit queue, which would silently drop packets and truncate tall images.
    private func sendRaw(
        _ data: Data,
        speed: PrintSpeed,
        onProgressPercent: ((Int) -> Void)? = nil
    ) async throws {
        guard let peripheral else { throw TiMiniPrinterError.notConnected }
        guard let char = writeChar else { throw TiMiniPrinterError.characteristicNotFound }

        let bytes = [UInt8](data)
        let chunk = speed.chunkSize

        var offset = 0
        var sentBytes = 0
        let totalBytes = bytes.count
        emitProgress(onProgressPercent, 0)
        while offset < bytes.count {
            // If the BLE transmit queue is full, suspend until peripheralIsReady fires.
            if !peripheral.canSendWriteWithoutResponse {
                await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
                    self.writeContinuation = cont
                }
            }

            let end   = min(offset + chunk, bytes.count)
            let slice = Data(bytes[offset..<end])
            peripheral.writeValue(slice, for: char, type: .withoutResponse)
            sentBytes += (end - offset)
            offset = end
            let percent = totalBytes == 0 ? 100 : (sentBytes * 100) / totalBytes
            emitProgress(onProgressPercent, percent)

            if offset < bytes.count && speed.intervalMs > 0 {
                try await Task.sleep(nanoseconds: speed.intervalMs * 1_000_000)
            }
        }
        emitProgress(onProgressPercent, 100)
    }

    private func emitProgress(_ callback: ((Int) -> Void)?, _ percent: Int) {
        callback?(max(0, min(100, percent)))
    }
}

// MARK: - CBCentralManagerDelegate

extension TiMiniPrinter: CBCentralManagerDelegate {

    public func centralManagerDidUpdateState(_ central: CBCentralManager) {
        guard central.state != .poweredOn else { return }
        // BT turned off during scan or mid-connect
        scanTimer?.invalidate()
        central.stopScan()
        scanOnEvent?(.bluetoothUnavailable)
        scanOnResult?([])
        scanOnEvent  = nil
        scanOnResult = nil
        connectContinuation?.resume(throwing: TiMiniPrinterError.bluetoothOff)
        connectContinuation = nil
    }

    public func centralManager(
        _ central: CBCentralManager,
        didDiscover peripheral: CBPeripheral,
        advertisementData: [String: Any],
        rssi RSSI: NSNumber
    ) {
        let rawName = peripheral.name
                   ?? advertisementData[CBAdvertisementDataLocalNameKey] as? String
                   ?? ""
        guard !rawName.isEmpty else { return }

        // Filter to known TiMini / thermal-printer name prefixes so random BLE
        // accessories don't pollute the list. Add more prefixes as needed.
        let knownPrefixes = ["X6", "X5", "X8", "A4", "MX", "T2", "M2", "GB",
                             "B21", "B1", "A200", "A300", "N2", "D110"]
        guard knownPrefixes.contains(where: { rawName.hasPrefix($0) }) else { return }

        let addr   = peripheral.identifier.uuidString
        let device = ScannedPrinter(name: rawName, address: addr)
        guard !scanResults.contains(device) else { return }

        scanResults.append(device)
        scanOnEvent?(.deviceFound(device))
    }

    public func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        peripheral.discoverServices([kServiceUUID])
    }

    public func centralManager(
        _ central: CBCentralManager,
        didFailToConnect peripheral: CBPeripheral,
        error: Error?
    ) {
        let reason = error?.localizedDescription ?? "Unknown error"
        connectContinuation?.resume(throwing: TiMiniPrinterError.connectionFailed(reason))
        connectContinuation = nil
    }

    public func centralManager(
        _ central: CBCentralManager,
        didDisconnectPeripheral peripheral: CBPeripheral,
        error: Error?
    ) {
        if let cont = connectContinuation {
            let reason = error?.localizedDescription ?? "Disconnected before ready"
            cont.resume(throwing: TiMiniPrinterError.connectionFailed(reason))
            connectContinuation = nil
        }
    }
}

// MARK: - CBPeripheralDelegate

extension TiMiniPrinter: CBPeripheralDelegate {

    public func peripheral(
        _ peripheral: CBPeripheral,
        didDiscoverServices error: Error?
    ) {
        if let err = error {
            connectContinuation?.resume(throwing: err)
            connectContinuation = nil
            return
        }

        let svc = peripheral.services?.first(where: { $0.uuid == kServiceUUID })
               ?? peripheral.services?.first

        guard let svc else {
            connectContinuation?.resume(throwing: TiMiniPrinterError.serviceNotFound)
            connectContinuation = nil
            return
        }

        peripheral.discoverCharacteristics([kWriteCharUUID, kNotifyCharUUID], for: svc)
    }

    public func peripheral(
        _ peripheral: CBPeripheral,
        didDiscoverCharacteristicsFor service: CBService,
        error: Error?
    ) {
        if let err = error {
            connectContinuation?.resume(throwing: err)
            connectContinuation = nil
            return
        }

        let chars = service.characteristics ?? []

        // Prefer ae01 (write-without-response), fall back to any writable char
        writeChar = chars.first(where: { $0.uuid == kWriteCharUUID })
            ?? chars.first(where: { $0.properties.contains(.writeWithoutResponse) })
            ?? chars.first(where: { $0.properties.contains(.write) })

        guard writeChar != nil else {
            connectContinuation?.resume(throwing: TiMiniPrinterError.characteristicNotFound)
            connectContinuation = nil
            return
        }

        // Subscribe to ae02 notify — required for X-series printers to start sending data
        notifyChar = chars.first(where: { $0.uuid == kNotifyCharUUID })
            ?? chars.first(where: { $0.properties.contains(.notify) })

        if let nc = notifyChar {
            peripheral.setNotifyValue(true, for: nc)
        }

        connectContinuation?.resume()
        connectContinuation = nil
    }

    public func peripheral(
        _ peripheral: CBPeripheral,
        didUpdateNotificationStateFor characteristic: CBCharacteristic,
        error: Error?
    ) {
        // Notification subscription confirmed — no additional action needed.
    }

    public func peripheralIsReady(toSendWriteWithoutResponse peripheral: CBPeripheral) {
        // Fired when the CoreBluetooth transmit queue drains — resume a waiting sendRaw.
        writeContinuation?.resume()
        writeContinuation = nil
    }
}
