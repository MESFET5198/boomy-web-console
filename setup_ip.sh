#!/bin/bash
sudo ip addr flush dev eth0
sudo ip addr add 192.168.55.101/24 dev eth0
sudo ip link set eth0 up
ip -4 addr show eth0
