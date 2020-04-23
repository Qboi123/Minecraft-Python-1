# Python packages
from _socket import SHUT_RDWR
import socket
import struct
import time
import timer

try:  # Python 3
    import socketserver
except ImportError:  # Python 2
    import socketserver as socketserver
import threading
# Third-party packages

# Modules from this project
import globals as G
# noinspection PyUnresolvedReferences
from savingsystem import save_sector_to_string, save_blocks, save_world, load_player, save_player
from world_server import WorldServer
# noinspection PyUnresolvedReferences
import blocks
from advUtils.network import PackageSystem
# from subprocess import
# noinspection PyUnresolvedReferences
from commands import CommandParser, COMMAND_ERROR_COLOR, COMMAND_NOT_HANDLED, COMMAND_HANDLED, COMMAND_INFO_COLOR, \
    CommandException
from utils import sectorize, make_string_packet
from mod import load_modules


# This class is effectively a serverside "Player" object
class ThreadedTCPRequestHandler(socketserver.BaseRequestHandler):
    inv = None

    def get_inv(self):
        global inv
        return inv

    def set_inv(self, value):
        global inv
        inv = value
        # if type(value[1]) == bytes:
        # raise Exception("")
        print("INVENTORY_EX:", value)

    inventory = property(get_inv, set_inv)  # "\0" * (4 * 40)  # Currently, is serialized to be 4 bytes * (27 inv + 9 quickbar + 4 armor) = 160 bytes
    command_parser = CommandParser()

    operator = False

    def __init__(self, *args, **kwargs):
        super(ThreadedTCPRequestHandler, self).__init__(*args, **kwargs)
        self.packageSystem = PackageSystem(self.request)

    def sendpacket(self, size, packet):
        # py_000002 = struct.pack("i", 5 + size)
        # print(py_000002, packet)
        # if type(packet) == str:
        #     packet = packet.encode("utf-8")
        # self.request.sendall(py_000002 + packet)
        if not hasattr(self, "packageSystem"):
            self.packageSystem = PackageSystem(self.request)
        print("SENDPACKET_SERVER:", packet) if packet["packetType"] != 1 and packet["packetType"] != 2 else None
        # if packet["packetType"] == 6:
        #     exit(0)
        self.packageSystem: PackageSystem
        self.packageSystem.sendall(packet)

    def sendchat(self, txt, color=(255, 255, 255, 255)):
        txt = txt.encode('utf-8')
        self.sendpacket(None, {
            "packetType": 5,
            "packet": {
                "message": txt,
                "color": color
            }
        })
        # self.sendpacket(len(txt) + 4, b"\5" + txt + struct.pack("BBBB", *color))

    def sendinfo(self, info, color=(255, 255, 255, 255)):
        info = info.encode('utf-8')
        self.sendpacket(None, {
            "packetType": 5,
            "packet": {
                "message": info,
                "color": color
            }
        })
        # self.sendpacket(len(info) + 4, "\5" + info + struct.pack("BBBB", *color))

    def broadcast(self, txt):
        for player in self.server.players.values():
            player.sendchat(txt)

    def sendpos(self, pos_bytes, mom_bytes, *, momentum, position):
        self.sendpacket(None, {
            "packetType": 8,
            "packet": {
                "playerID": self.id,
                "momentum": momentum,
                "position": position
            }
        })
        # self.sendpacket(38, "\x08" + struct.pack("H", self.id) + mom_bytes + pos_bytes)

    def lookup_player(self, playername):
        # find player by name
        for player in list(self.server.players.values()):
            if player.username == playername:
                return player
        return None

    def handle(self):
        self.username = str(self.client_address)
        print("Client connecting...", self.client_address)
        self.server.players[self.client_address] = self
        self.server.player_ids.append(self)
        self.id = len(self.server.player_ids) - 1
        try:
            self.loop()
        except socket.error as e:
            if self.server._stop.isSet():
                return  # Socket error while shutting down doesn't matter
            if e[0] in (10053, 10054):
                print("Client %s %s crashed." % (self.username, self.client_address))
            else:
                raise e

    def loop(self):
        world, players = self.server.world, self.server.players
        package_system = PackageSystem(self.request)
        while 1:
            # byte = self.request.recv(1)
            data = package_system.recv()
            # print("Server recieved packet: %s" % data)
            packettype = data["packetType"]
            packet = data["packet"]
            print(f"SERVERPACKET:", data) if packettype != 1 else None
            # if not byte: return  # The client has disconnected intentionally

            # packettype = struct.unpack("B", byte)[0]  # Client Packet Type
            if packettype == 1:  # Sector request
                # sector = struct.unpack("iii", self.request.recv(4 * 3))
                sector = packet["sector"]

                # print("SECTORCHANGE_CURRENT:", sector)
                # print("SECTORCHANGE_ALL:", world.sectors)
                print("SECTORIZE_NEW:", sector in world.sectors)

                if sector not in world.sectors:
                    with world.server_lock:
                        world.open_sector(sector)

                if not world.sectors[sector]:
                    # Empty sector, send packet 2
                    self.sendpacket(None, {
                        "packetType": 2,
                        "packet": {
                            "sector": sector
                        }
                    })
                    # self.sendpacket(12, b"\2" + struct.pack("iii", *sector))
                else:
                    # py_000005 = save_sector_to_string(world, sector)
                    # py_000003 = (py_000005.encode("utf-8") if type(py_000005) == str else py_000005)
                    # py_000004 = world.get_exposed_sector(sector).encode("utf-8")
                    # msg = struct.pack("iii", *sector) + py_000003 + py_000004
                    self.sendpacket(None, {
                        "packetType": 1,
                        "packet": {
                            "sector": sector,
                            "exposedSector": world.get_exposed_sector(sector),
                            "sectorData": save_sector_to_string(world, sector)
                        }
                    })
                    # self.sendpacket(len(msg), b"\1" + msg)
            elif packettype == 3:  # Add block
                # positionbytes = self.request.recv(4 * 3)
                # blockbytes = self.request.recv(2)
                position = packet["position"]
                blockid = G.BLOCKS_DIR[packet["block"]]

                # position = struct.unpack("iii", positionbytes)
                # blockid = G.BLOCKS_DIR[struct.unpack("BB", blockbytes)]
                with world.server_lock:
                    world.add_block(position, blockid, sync=False)

                for address in players:
                    if address is self.client_address:
                        continue  # He told us, we don't need to tell him
                    players[address].packageSystem.send({
                        "packetType": 3,
                        "packet": {
                            "position": position,
                            "block": blockid
                        }
                    })
                    # players[address].sendpacket(14, "\3" + positionbytes + blockbytes)
            elif packettype == 4:  # Remove block
                # positionbytes = self.request.recv(4 * 3)

                with world.server_lock:
                    world.remove_block(packet["position"], sync=False)

                for address in players:
                    if address is self.client_address: continue  # He told us, we don't need to tell him
                    players[address].sendpacket(12, "\4" + positionbytes)
            elif packettype == 5:  # Receive chat text
                # txtlen = struct.unpack("i", self.request.recv(4))[0]
                # raw_txt = self.request.recv(txtlen).decode('utf-8')
                raw_txt = packet["message"].decode()
                txt = "%s: %s" % (self.username.decode(), raw_txt)
                try:
                    if raw_txt[0] == '/':
                        ex = self.command_parser.execute(raw_txt, user=self, world=world)
                        if ex != COMMAND_HANDLED:
                            self.sendchat('$$rUnknown command.')
                    else:
                        # Not a command, send the chat to all players
                        for address in players:
                            players[address].sendchat(txt)
                        print(txt)  # May as well let console see it too
                except CommandException as e:
                    self.sendchat(str(e), COMMAND_ERROR_COLOR)
            elif packettype == 6:  # Player Inventory Update
                print("SERVER_PACKET06:", packet)
                self.inventory = packet["items"]
                # TODO: All player's inventories should be autosaved at a regular interval.
                pass
            elif packettype == 8:  # Player Movement
                # mom_bytes, pos_bytes = self.request.recv(4 * 3), self.request.recv(8 * 3)
                # self.momentum = struct.unpack("fff", mom_bytes)
                # self.position = struct.unpack("ddd", pos_bytes)
                self.momentum = packet["momentum"]
                self.position = packet["position"]
                for address in players:
                    if address is self.client_address:
                        continue  # He told us, we don't need to tell him
                    # TODO: Only send to nearby players
                    self.sendpacket(None, {
                        "packetType": 8,
                        "packet": {
                            "playerID": self.id,
                            "momentum": self.momentum,
                            "position": self.position
                        }
                    })
                    # players[address].sendpacket(38, "\x08" + struct.pack("H", self.id) + mom_bytes + pos_bytes)
            elif packettype == 9:  # Player Jump
                # raise NotImplementedError("Player Jump not implemented")
                for address in players:
                    if address is self.client_address:
                        continue  # He told us, we don't need to tell him
                    # TODO: Only send to nearby players
                    players[address].sendpacket(None, {
                        "packetType": 9,
                        "package": {
                            "playerID": self.id
                        }
                    })
                    # players[address].sendpacket(2, "\x09" + struct.pack("H", self.id))
            elif packettype == 10:  # Update Tile Entity
                # block_pos = struct.unpack("iii", self.request.recv(4 * 3))
                # ent_size = struct.unpack("i", self.request.recv(4))[0]
                # world[block_pos].update_tile_entity(self.request.recv(ent_size))
                pass
            elif packettype == 255:  # Initial Login
                # txtlen = struct.unpack("i", self.request.recv(4))[0]
                # data2 = package_system.recv()
                self.username = packet["username"]
                # position = packet["position"]

                # self.username = self.request.recv(txtlen).decode('utf-8')
                self.position = None
                load_player(self, "world")

                for player in self.server.players.values():
                    player.sendchat("$$y%s has connected." % self.username)
                print("%s's username is %s" % (self.client_address, self.username))

                position = (0, self.server.world.terraingen.get_height(0, 0) + 2, 0)
                if self.position is None: self.position = position  # New player, set initial position

                # Send list of current players to the newcomer
                for player in self.server.players.values():
                    if player is self: continue
                    name = player.username.encode('utf-8')
                    self.sendpacket(None, {
                        "packetType": 7,
                        "packet": {
                            "playerID": player.id,
                            "username": name
                        }
                    })

                    # self.sendpacket(2 + len(name), '\7' + struct.pack("H", player.id) + name)
                # Send the newcomer's name to all current players
                name = self.username
                for player in self.server.players.values():
                    if player is self: continue
                    player.sendpacket(None, {
                        "packetType": 7,
                        "packet": {
                            "playerID": self.id,
                            "username": name
                        }
                    })

                    # player.sendpacket(2 + len(name), '\7' + struct.pack("H", self.id) + name)

                # Send them the sector under their feet first so they don't fall
                sector = sectorize(position)
                if sector not in world.sectors:
                    with world.server_lock:
                        world.open_sector(sector)
                py_000001 = struct.pack("iii", *sector)
                sector_string = save_sector_to_string(world, sector)
                exposed_sector = world.get_exposed_sector(sector)

                # print(py_000001, sector_string, exposed_sector)
                msg = py_000001 + sector_string + exposed_sector.encode('utf-8')
                self.sendpacket(None, {
                    "packetType": 1,
                    "packet": {
                        "sector": sector,
                        "exposedSector": exposed_sector,
                        "sectorData": sector_string
                    }
                })
                # self.sendpacket(len(msg), b"\1" + msg)

                # Send them their spawn position and world seed(for client side biome generator)
                seed_packet = make_string_packet(G.SEED)

                self.sendpacket(None, {
                    "packetType": 255,
                    "packet": {
                        "position": position,
                        "seed": G.SEED
                    }
                })
                # self.sendpacket(12 + len(seed_packet),
                #                 struct.pack("B", 255) + struct.pack("iii", *position) + seed_packet)

                print("IMPORTANT0004:", self.inventory)
                print("IMPORTANT0005:", len(self.inventory)+1)
                self.sendpacket(None, {
                    "packetType": 6,
                    "packet": {
                        "items": self.inventory
                    }
                })
                # self.sendpacket(len(self.inventory)+1, "\6" + self.inventory)
            else:
                print("Received unknown packettype", packettype)

    def finish(self):
        print("Client disconnected,", self.client_address, self.username)
        try:
            del self.server.players[self.client_address]
        except KeyError:
            pass
        for player in self.server.players.values():
            player.sendchat("%s has disconnected." % self.username)
        # Send user list
        for player in self.server.players.values():
            player.sendpacket(2 + 1, '\7' + struct.pack("H", self.id) + '\0')
        save_player(self, "world")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(self, *args, **kwargs):
        socketserver.ThreadingTCPServer.__init__(self, *args, **kwargs)
        self._stop = threading.Event()

        self.world = WorldServer(self)
        self.players = {}  # Dict of all players connected. {ipaddress: requesthandler,}
        self.player_ids = []  # List of all players this session, indexes are their ID's [0: first requesthandler,]

        self.command_parser = CommandParser()

    def show_block(self, position, block):
        blockid = block.id
        for player in self.players.values():
            # TODO: Only if they're in range
            player.sendpacket(None, {
                "packetType": 3,
                "packet": {
                    "position": position,
                    "block": (blockid.main, blockid.sub)
                }
            })
            # player.sendpacket(14, "\3" + struct.pack("iiiBB", *(position + (blockid.main, blockid.sub))))

    def hide_block(self, position):
        for player in self.players.values():
            # TODO: Only if they're in range
            player.sendpacket(12, "\4" + struct.pack("iii", *position))

    def update_tile_entity(self, position, value):
        for player in self.players.values():
            player.sendpacket(12 + len(value), "\x0A" + struct.pack("iii", *position) + value)


def start_server(internal=False):
    if internal:
        server = Server(("localhost", 1486), ThreadedTCPRequestHandler)
    else:
        localip = [ip for ip in socket.gethostbyname_ex(socket.gethostname())[2] if not ip.startswith("127.")][0]
        server = Server((localip, 1486), ThreadedTCPRequestHandler)
    G.SERVER = server
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.start()

    threading.Thread(target=server.world.content_update, name="world_server.content_update").start()

    # start server timer
    G.main_timer = timer.Timer(G.TIMER_INTERVAL, name="G.main_timer")
    G.main_timer.start()

    return server, server_thread


if __name__ == '__main__':
    # TODO: Enable server launch options
    # In the mean time, manually set
    setattr(G.LAUNCH_OPTIONS, "seed", None)
    G.SAVE_FILENAME = "world"

    load_modules(server=True)

    server, server_thread = start_server(internal=True)
    print(('Server loop running in thread: ' + server_thread.name))

    ip, port = server.server_address
    print("Listening on", ip, port)

    helptext = "Available commands: " + ", ".join(["say", "stop", "save"])
    while 1:
        args = input().replace(chr(13), "").split(" ")  # On some systems CR is appended, gotta remove that
        cmd = args.pop(0)
        if cmd == "say":
            msg = "Server: %s" % " ".join(args)
            print(msg)
            for player in server.players.values():
                player.sendchat(msg, color=(180, 180, 180, 255))
        elif cmd == "help":
            print(helptext)
        elif cmd == "save":
            print("Saving...")
            save_world(server, "world")
            print("Done saving")
        elif cmd == "stop":
            server._stop.set()
            G.main_timer.stop()
            print("Disconnecting clients...")
            for address in server.players:
                try:
                    server.players[address].request.shutdown(SHUT_RDWR)
                    server.players[address].request.close()
                except socket.error:
                    pass
            print("Shutting down socket...")
            server.shutdown()
            print("Saving...")
            save_world(server, "world")
            print("Goodbye")
            break
        else:
            print("Unknown command '%s'." % cmd, helptext)
    while len(threading.enumerate()) > 1:
        threads = threading.enumerate()
        threads.remove(threading.current_thread())
        print("Waiting on these threads to close:", threads)
        time.sleep(1)
