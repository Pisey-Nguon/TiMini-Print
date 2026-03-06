//
//  ContentView.swift
//  Mini Portable Printer
//
//  Created by Pisey Nguon on 3/3/26.
//

import SwiftUI
import UIKit
import Combine
import PhotosUI

@MainActor
final class PrinterViewModel: ObservableObject {
    @Published var devices: [ScannedPrinter] = []
    @Published var isScanning = false
    @Published var status: String = "Idle"
    @Published var connectedDevice: ScannedPrinter?
    @Published var isConnected = false
    @Published var printTextInput: String = "Hello World!\nSecond line."
    @Published var pickedImage: UIImage? = nil

    private var printer = TiMiniPrinter()

    func scan() {
        guard !isScanning else { return }
        status = "Scanning…"
        isScanning = true
        devices.removeAll()

        printer.scan(timeoutSeconds: 8, onEvent: { [weak self] event in
            guard let self else { return }
            switch event {
            case .deviceFound(let d):
                if !self.devices.contains(d) { self.devices.append(d) }
            case .scanFailed(_, let reason):
                self.status = "Scan failed: \(reason)"
            case .bluetoothUnavailable:
                self.status = "Bluetooth unavailable or off"
            }
        }, onResult: { [weak self] results in
            guard let self else { return }
            self.devices = results
            self.isScanning = false
            if results.isEmpty {
                self.status = "No printers found"
            } else {
                self.status = "Found \(results.count) device(s)"
            }
        })
    }

    func connect(to device: ScannedPrinter) {
        Task { [weak self] in
            guard let self else { return }
            self.status = "Connecting to \(device.name)…"
            do {
                try await self.printer.connect(address: device.address)
                self.connectedDevice = device
                self.isConnected = true
                self.status = "Connected to \(device.name)"
            } catch {
                self.status = "Connect failed: \(error.localizedDescription)"
                self.isConnected = false
                self.connectedDevice = nil
            }
        }
    }

    func disconnect() {
        printer.disconnect()
        isConnected = false
        status = "Disconnected"
    }

    func printText() {
        guard isConnected else { status = "Not connected"; return }
        Task { [weak self] in
            guard let self else { return }
            do {
                let opts = PrintOptions(depth: .dark, speed: .high, copies: 1, textSize: .medium)
                try await self.printer.printText(self.printTextInput, options: opts)
                self.status = "Printed text"
            } catch {
                self.status = "Print failed: \(error.localizedDescription)"
            }
        }
    }

    func printPickedImage() {
        guard isConnected else { status = "Not connected"; return }
        guard let image = pickedImage else { status = "No image selected"; return }
        Task { [weak self] in
            guard let self else { return }
            do {
                let opts = PrintOptions(depth: .dark, speed: .high, copies: 1)
                try await self.printer.printImage(image, options: opts)
                self.status = "Printed image"
            } catch {
                self.status = "Print failed: \(error.localizedDescription)"
            }
        }
    }
}

struct ContentView: View {
    @StateObject private var vm = PrinterViewModel()
    @State private var photoItem: PhotosPickerItem?

    var body: some View {
        NavigationStack {
            VStack(spacing: 16) {
                HStack {
                    Button(action: vm.scan) {
                        Label(vm.isScanning ? "Scanning…" : "Scan", systemImage: vm.isScanning ? "hourglass" : "dot.radiowaves.left.and.right")
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(vm.isScanning)

                    if vm.isConnected {
                        Button("Disconnect", role: .destructive, action: vm.disconnect)
                            .buttonStyle(.bordered)
                    }
                }

                List {
                    Section("Discovered Printers") {
                        if vm.devices.isEmpty {
                            Text("No devices yet — tap Scan").foregroundStyle(.secondary)
                        } else {
                            ForEach(vm.devices, id: \.address) { device in
                                HStack {
                                    VStack(alignment: .leading) {
                                        Text(device.name).font(.headline)
                                        Text(device.address).font(.caption).foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    if vm.connectedDevice?.address == device.address, vm.isConnected {
                                        Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                                    } else {
                                        Button("Connect") { vm.connect(to: device) }
                                            .buttonStyle(.bordered)
                                    }
                                }
                            }
                        }
                    }

                    Section("Print Text") {
                        TextEditor(text: $vm.printTextInput)
                            .frame(minHeight: 100)
                            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.gray.opacity(0.2)))
                        Button("Print Text") { vm.printText() }
                            .buttonStyle(.borderedProminent)
                            .disabled(!vm.isConnected)
                    }

                    Section("Print Image") {
                        PhotosPicker(
                            selection: $photoItem,
                            matching: .images,
                            photoLibrary: .shared()
                        ) {
                            Label("Choose from Gallery", systemImage: "photo.on.rectangle")
                        }
                        .onChange(of: photoItem) { newItem in
                            Task {
                                if let data = try? await newItem?.loadTransferable(type: Data.self),
                                   let uiImage = UIImage(data: data) {
                                    vm.pickedImage = uiImage
                                }
                            }
                        }

                        if let img = vm.pickedImage {
                            Image(uiImage: img)
                                .resizable()
                                .scaledToFit()
                                .cornerRadius(8)

                            Button("Print Image") { vm.printPickedImage() }
                                .buttonStyle(.borderedProminent)
                                .disabled(!vm.isConnected)
                        }
                    }
                }
                .listStyle(.insetGrouped)

                Text(vm.status)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .padding(.bottom, 8)
            }
            .navigationTitle("Mini Printer")
        }
    }
}

#Preview {
    ContentView()
}
