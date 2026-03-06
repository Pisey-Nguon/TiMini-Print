#!/usr/bin/env python3
"""Inspect BLE GATT services and characteristics on a printer."""
import asyncio
import sys

ADDRESS = "F464B34D-0F9E-CD40-E0F4-8820645F0A23"  # X6h-B98D UUID

async def inspect(address: str) -> None:
    try:
        from bleak import BleakClient
    except ImportError:
        print("ERROR: bleak not installed")
        sys.exit(1)

    print(f"Connecting to {address} ...")
    async with BleakClient(address) as client:
        print(f"Connected: {client.is_connected}\n")
        for service in client.services:
            print(f"SERVICE: {service.uuid}  ({service.description})")
            for char in service.characteristics:
                props = ", ".join(char.properties)
                print(f"  CHAR: {char.uuid}  props=[{props}]  ({char.description})")
                for desc in char.descriptors:
                    print(f"    DESC: {desc.uuid}  ({desc.description})")
            print()

asyncio.run(inspect(ADDRESS))
