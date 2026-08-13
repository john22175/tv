import socket

msg = "\r\n".join([
    "M-SEARCH * HTTP/1.1",
    "HOST:239.255.255.250:1900",
    'MAN:"ssdp:discover"',
    "MX:2",
    "ST:ssdp:all",
    "",
    "",
]).encode()

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(3)

sock.sendto(msg, ("239.255.255.250",1900))

while True:
    try:
        data, addr = sock.recvfrom(65535)
        print("="*60)
        print(addr)
        print(data.decode(errors="ignore"))
    except socket.timeout:
        break