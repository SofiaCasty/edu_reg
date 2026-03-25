import os
import socket
import threading


LISTEN_HOST = "127.0.0.1"
DEFAULT_LISTEN_PORT = 3307
LISTEN_PORT = int(os.environ.get("PROXY_PORT", DEFAULT_LISTEN_PORT))
TARGET_HOST = "35.222.28.57"
TARGET_PORT = 3306


def pipe(source: socket.socket, target: socket.socket) -> None:
    try:
        while True:
            data = source.recv(65536)
            if not data:
                break
            target.sendall(data)
    except Exception:
        pass
    finally:
        try:
            target.shutdown(socket.SHUT_WR)
        except Exception:
            pass


def handle_client(client: socket.socket) -> None:
    try:
        remote = socket.create_connection((TARGET_HOST, TARGET_PORT), timeout=15)
    except Exception:
        client.close()
        return

    threading.Thread(target=pipe, args=(client, remote), daemon=True).start()
    threading.Thread(target=pipe, args=(remote, client), daemon=True).start()


def main() -> None:
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LISTEN_HOST, LISTEN_PORT))
    server.listen(50)
    print(f"MySQL proxy escuchando en {LISTEN_HOST}:{LISTEN_PORT} -> {TARGET_HOST}:{TARGET_PORT}")
    while True:
        client, _ = server.accept()
        threading.Thread(target=handle_client, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()
