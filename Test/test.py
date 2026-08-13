import socket
import time
import urllib.request
import ssl
import ipaddress
import concurrent.futures


SSDP_TARGETS = [
    "ssdp:all",
    "upnp:rootdevice",
    "urn:schemas-upnp-org:device:MediaRenderer:1",
    "urn:schemas-upnp-org:service:AVTransport:1",
]


def ssdp_search(st, timeout=3):
    msg = "\r\n".join([
        "M-SEARCH * HTTP/1.1",
        "HOST: 239.255.255.250:1900",
        'MAN: "ssdp:discover"',
        "MX: 2",
        f"ST: {st}",
        "",
        "",
    ]).encode("utf-8")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.settimeout(timeout)

    results = []

    try:
        sock.sendto(msg, ("239.255.255.250", 1900))
        started = time.time()

        while time.time() - started < timeout:
            try:
                data, addr = sock.recvfrom(65535)
                text = data.decode(errors="ignore")
                results.append((addr[0], text))
            except socket.timeout:
                break
    finally:
        sock.close()

    return results


def get_header_value(response_text, header):
    for line in response_text.splitlines():
        if line.lower().startswith(header.lower() + ":"):
            return line.split(":", 1)[1].strip()
    return None


def fetch_url(url, timeout=3):
    try:
        context = ssl._create_unverified_context()
        req = urllib.request.Request(url, headers={"User-Agent": "TV-Discovery-Test"})
        with urllib.request.urlopen(req, timeout=timeout, context=context) as r:
            return r.status, r.read(3000).decode(errors="ignore")
    except Exception as e:
        return None, str(e)


def test_samsung_api(ip):
    urls = [
        f"http://{ip}:8001/api/v2/",
        f"https://{ip}:8002/api/v2/",
    ]

    found = []

    for url in urls:
        status, body = fetch_url(url, timeout=2)
        if status:
            found.append((url, status, body[:500]))

    return found


def get_local_subnet():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    finally:
        s.close()

    # Assumes /24 for testing.
    subnet = ipaddress.ip_network(ip + "/24", strict=False)
    return ip, subnet


def main():
    print("=== SSDP / UPnP scan ===")

    seen_locations = {}

    for target in SSDP_TARGETS:
        print(f"\nSearching ST: {target}")
        responses = ssdp_search(target, timeout=3)

        if not responses:
            print("  No responses.")
            continue

        for ip, text in responses:
            st = get_header_value(text, "ST")
            location = get_header_value(text, "LOCATION") or get_header_value(text, "Location")
            server = get_header_value(text, "SERVER") or get_header_value(text, "Server")

            print(f"\n  Device response from {ip}")
            print(f"  ST: {st}")
            print(f"  LOCATION: {location}")
            print(f"  SERVER: {server}")

            if location:
                seen_locations[location] = ip

    print("\n=== Fetching UPnP device descriptions ===")

    if not seen_locations:
        print("No LOCATION URLs found from SSDP.")
    else:
        for location, ip in seen_locations.items():
            print(f"\nFetching {location}")
            status, body = fetch_url(location, timeout=3)

            if not status:
                print(f"  Failed: {body}")
                continue

            lower = body.lower()
            print(f"  HTTP {status}")

            if "friendlyname" in lower:
                start = lower.find("<friendlyname>")
                end = lower.find("</friendlyname>")
                print(f"  friendlyName: {body[start + 14:end] if start != -1 and end != -1 else 'found but not parsed'}")

            if "manufacturer" in lower:
                start = lower.find("<manufacturer>")
                end = lower.find("</manufacturer>")
                print(f"  manufacturer: {body[start + 14:end] if start != -1 and end != -1 else 'found but not parsed'}")

            if "avtransport" in lower:
                print("  AVTransport: YES - this may support DLNA playback")
            else:
                print("  AVTransport: no")

            if "mediarenderer" in lower:
                print("  MediaRenderer: YES")
            else:
                print("  MediaRenderer: no")

            if "samsung" in lower:
                print("  Samsung text found in description")

    print("\n=== Samsung local API scan ===")
    local_ip, subnet = get_local_subnet()
    print(f"Local IP: {local_ip}")
    print(f"Scanning subnet: {subnet}")

    samsung_hits = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as executor:
        futures = {
            executor.submit(test_samsung_api, str(ip)): str(ip)
            for ip in subnet.hosts()
        }

        for future in concurrent.futures.as_completed(futures):
            ip = futures[future]
            try:
                hits = future.result()
            except Exception:
                continue

            for url, status, body in hits:
                print(f"\nPossible Samsung TV API found:")
                print(f"  {url}")
                print(f"  HTTP {status}")
                print(f"  Body preview: {body[:300]}")
                samsung_hits.append((ip, url))

    if not samsung_hits:
        print("\nNo Samsung local API endpoints found on this /24 subnet.")

    print("\n=== Done ===")


if __name__ == "__main__":
    main()