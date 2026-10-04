import argparse
import base64
import hashlib
import json
from pathlib import Path

import cv2
import qrcode


# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

# Raw file bytes per QR packet
CHUNK_SIZE = 1800

MAGIC = "QRFT"
VERSION = 1


# ------------------------------------------------------------
# Encoding
# ------------------------------------------------------------

def encode_file(input_file, output_dir):
    input_path = Path(input_file)
    output_path = Path(output_dir)

    output_path.mkdir(parents=True, exist_ok=True)

    # Read original file as raw bytes
    data = input_path.read_bytes()

    file_size = len(data)
    file_hash = hashlib.sha256(data).hexdigest()

    # Split file into raw-byte chunks
    chunks = [
        data[i:i + CHUNK_SIZE]
        for i in range(0, file_size, CHUNK_SIZE)
    ]

    total_chunks = len(chunks)

    print(f"File       : {input_path}")
    print(f"Size       : {file_size} bytes")
    print(f"Chunk size : {CHUNK_SIZE} bytes")
    print(f"QR count   : {total_chunks}")
    print(f"SHA-256    : {file_hash}")
    print()

    for chunk_id, chunk in enumerate(chunks):

        # Base64 gives ~33% overhead instead of hex's ~100%
        encoded_data = base64.b64encode(chunk).decode("ascii")

        packet = {
            "magic": MAGIC,
            "version": VERSION,
            "filename": input_path.name,
            "chunk_id": chunk_id,
            "total_chunks": total_chunks,
            "file_size": file_size,
            "sha256": file_hash,
            "data": encoded_data,
        }

        # Compact JSON
        packet_bytes = json.dumps(
            packet,
            separators=(",", ":")
        ).encode("utf-8")

        # Create QR
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )

        qr.add_data(packet_bytes)
        qr.make(fit=True)

        img = qr.make_image()

        filename = output_path / f"qr_{chunk_id:06d}.png"
        img.save(filename)

        print(
            f"[{chunk_id + 1}/{total_chunks}] "
            f"chunk={chunk_id} "
            f"raw={len(chunk)} B "
            f"base64={len(encoded_data)} B "
            f"QR={filename}"
        )

    print()
    print("Encoding complete.")


# ------------------------------------------------------------
# Decode a single QR image
# ------------------------------------------------------------

def decode_qr_image(image_path):
    image = cv2.imread(str(image_path))

    if image is None:
        raise ValueError(
            f"Cannot read image: {image_path}"
        )

    detector = cv2.QRCodeDetector()

    data, points, _ = detector.detectAndDecode(image)

    if not data:
        raise ValueError(
            f"No QR code found in {image_path}"
        )

    packet = json.loads(data)

    # Validate packet
    if packet.get("magic") != MAGIC:
        raise ValueError(
            f"Invalid QR packet in {image_path}"
        )

    if packet.get("version") != VERSION:
        raise ValueError(
            f"Unsupported packet version in {image_path}"
        )

    # Decode Base64 back to raw bytes
    try:
        chunk = base64.b64decode(
            packet["data"],
            validate=True
        )
    except Exception as e:
        raise ValueError(
            f"Invalid Base64 payload in {image_path}: {e}"
        )

    packet["decoded_data"] = chunk

    return packet


# ------------------------------------------------------------
# Decode directory of QR images
# ------------------------------------------------------------

def decode_directory(input_dir, output_file=None):
    input_path = Path(input_dir)

    images = sorted(input_path.glob("*.png"))

    if not images:
        raise ValueError(
            f"No PNG files found in {input_path}"
        )

    chunks = {}

    total_chunks = None
    expected_hash = None
    filename = None
    file_size = None

    print(f"Found {len(images)} QR images")
    print()

    for image in images:

        try:
            packet = decode_qr_image(image)

        except Exception as e:
            print(f"[ERROR] {image}: {e}")
            continue

        chunk_id = packet["chunk_id"]

        # Initialize metadata from first valid packet
        if total_chunks is None:
            total_chunks = packet["total_chunks"]
            expected_hash = packet["sha256"]
            filename = packet["filename"]
            file_size = packet["file_size"]

        # ----------------------------------------------------
        # Validate metadata
        # ----------------------------------------------------

        if packet["total_chunks"] != total_chunks:
            raise ValueError(
                f"Total chunk mismatch in {image}"
            )

        if packet["sha256"] != expected_hash:
            raise ValueError(
                f"SHA-256 metadata mismatch in {image}"
            )

        if packet["file_size"] != file_size:
            raise ValueError(
                f"File size mismatch in {image}"
            )

        # ----------------------------------------------------
        # Store chunk
        # ----------------------------------------------------

        if chunk_id in chunks:
            print(
                f"[WARNING] Duplicate chunk {chunk_id}"
            )
            continue

        chunks[chunk_id] = packet["decoded_data"]

        print(
            f"[{len(chunks)}/{total_chunks}] "
            f"received chunk {chunk_id} "
            f"({len(packet['decoded_data'])} bytes)"
        )

    print()

    # --------------------------------------------------------
    # Check missing chunks
    # --------------------------------------------------------

    missing = [
        i for i in range(total_chunks)
        if i not in chunks
    ]

    if missing:
        print("Decoding failed.")
        print(
            f"Received {len(chunks)}/{total_chunks} chunks"
        )

        print(f"Missing chunks: {missing}")

        return

    # --------------------------------------------------------
    # Reassemble in correct order
    # --------------------------------------------------------

    reconstructed = b"".join(
        chunks[i]
        for i in range(total_chunks)
    )

    # --------------------------------------------------------
    # Verify file size
    # --------------------------------------------------------

    if len(reconstructed) != file_size:
        raise ValueError(
            f"File size mismatch: "
            f"expected {file_size}, "
            f"got {len(reconstructed)}"
        )

    # --------------------------------------------------------
    # Verify SHA-256
    # --------------------------------------------------------

    actual_hash = hashlib.sha256(
        reconstructed
    ).hexdigest()

    print(f"Expected SHA-256 : {expected_hash}")
    print(f"Actual SHA-256   : {actual_hash}")

    if actual_hash != expected_hash:
        raise ValueError(
            "SHA-256 verification FAILED"
        )

    # --------------------------------------------------------
    # Determine output filename
    # --------------------------------------------------------

    if output_file is None:
        output_file = input_path / (
            "decoded_" + filename
        )

    output_file = Path(output_file)

    # Write reconstructed file
    output_file.write_bytes(reconstructed)

    print()
    print("Decoding complete.")
    print(f"Output : {output_file}")
    print(f"Size   : {len(reconstructed)} bytes")
    print("SHA-256 verification: PASSED")


# ------------------------------------------------------------
# Command-line interface
# ------------------------------------------------------------

def main():

    parser = argparse.ArgumentParser(
        description="QR-based file transfer"
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True
    )

    # --------------------------------------------------------
    # Encode command
    # --------------------------------------------------------

    encode_parser = subparsers.add_parser(
        "encode",
        help="Convert a file into QR images"
    )

    encode_parser.add_argument(
        "input",
        help="Input file"
    )

    encode_parser.add_argument(
        "output",
        help="Directory for QR images"
    )

    # --------------------------------------------------------
    # Decode command
    # --------------------------------------------------------

    decode_parser = subparsers.add_parser(
        "decode",
        help="Convert QR images back into a file"
    )

    decode_parser.add_argument(
        "input",
        help="Directory containing QR images"
    )

    decode_parser.add_argument(
        "-o",
        "--output",
        help="Output filename",
        default=None
    )

    args = parser.parse_args()

    if args.command == "encode":

        encode_file(
            args.input,
            args.output
        )

    elif args.command == "decode":

        decode_directory(
            args.input,
            args.output
        )


if __name__ == "__main__":
    main()
