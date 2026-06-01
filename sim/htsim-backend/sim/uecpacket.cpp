#include "uecpacket.h"

PacketDB<UecPacket> UecPacket::_packetdb;
PacketDB<UecAck> UecAck::_packetdb;
PacketDB<UecNack> UecNack::_packetdb;
PacketDB<UecMcastPacket> UecMcastPacket::_packetdb;
PacketDB<UecReducePacket> UecReducePacket::_packetdb;
