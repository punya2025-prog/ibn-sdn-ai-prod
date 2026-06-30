#!/usr/bin/env python3
"""
generate_extended_configs.py
==============================
Generates EOS startup configs for extended topology with:
  - 4 new server VLANs (1400 DB, 1500 App, 1600 Storage, 1700 Backup)
  - WiFi VLANs (1800 Corp, 1900 Guest)
  - CCTV on VLAN 1100
  - Port descriptions as labels: WiFi-AP, CCTV-Camera, Server, IP-Phone etc.
  - Management interface, SSH, eAPI, NETCONF on every switch

Run:
  python3 generate_extended_configs.py
"""

from pathlib import Path

OUT = Path("topology/configs/extended")
OUT.mkdir(parents=True, exist_ok=True)

ALL_VLANS = list(range(100, 2000, 100))  # 100-1900

def mgmt_header(hostname, mgmt_ip):
    return f"""hostname {hostname}
!
username admin privilege 15 role network-admin secret admin
!
interface Management0
   ip address {mgmt_ip}/24
   no shutdown
!
management ssh
   idle-timeout 0
   authentication mode password
   no shutdown
!
management api http-commands
   protocol http
   no shutdown
!
management api netconf
   transport ssh default
   no shutdown
!
lldp run
!
"""

def vlan_block(vlans):
    return "\n".join(f"vlan {v}" for v in vlans) + "\n!\n"

def trunk_vlans(vlans):
    return ",".join(str(v) for v in vlans)

# ── Spines ────────────────────────────────────────────────────────────────────
def gen_spine(num, mgmt_ip, lo_ip, asn, neighbors):
    cfg = mgmt_header(f"dc-spine{num}", mgmt_ip)
    cfg += f"interface Loopback0\n   ip address {lo_ip}/32\n!\n"
    for i, (peer_ip, peer_asn, desc, local_ip) in enumerate(neighbors, start=49):
        cfg += f"""interface Ethernet{i}
   description To {desc}
   no switchport
   ip address {local_ip}/31
   no shutdown
!
"""
    cfg += "ip routing\n!\n"
    cfg += f"router bgp {asn}\n   router-id {lo_ip}\n   maximum-paths 4\n"
    for peer_ip, peer_asn, desc, _ in neighbors:
        cfg += f"   neighbor {peer_ip} remote-as {peer_asn}\n"
        cfg += f"   neighbor {peer_ip} description {desc}\n"
    cfg += "   redistribute connected\n!\nend\n"
    return cfg

# ── Leaves ────────────────────────────────────────────────────────────────────
def gen_leaf(num, mgmt_ip, lo_ip, asn, s1_peer, s1_local, s2_peer, s2_local, vlans):
    cfg  = mgmt_header(f"dc-leaf{num}", mgmt_ip)
    cfg += f"interface Loopback0\n   ip address {lo_ip}/32\n!\n"
    cfg += vlan_block(vlans)
    cfg += f"""interface Ethernet49
   description To dc-spine1 uplink
   no switchport
   ip address {s1_local}/31
   no shutdown
!
interface Ethernet50
   description To dc-spine2 uplink
   no switchport
   ip address {s2_local}/31
   no shutdown
!
"""
    cfg += "ip routing\n!\n"
    cfg += f"router bgp {asn}\n   router-id {lo_ip}\n   maximum-paths 4\n"
    cfg += f"   neighbor {s1_peer} remote-as 65000\n"
    cfg += f"   neighbor {s2_peer} remote-as 65000\n"
    cfg += "   redistribute connected\n!\nend\n"
    return cfg

# ── Campus core ───────────────────────────────────────────────────────────────
def gen_campus_core():
    cfg  = mgmt_header("campus-core1", "10.200.0.31")
    cfg += vlan_block(ALL_VLANS)
    tv   = trunk_vlans(ALL_VLANS)
    cfg += f"""interface Ethernet49
   description To dc-leaf4 Eth51
   switchport mode trunk
   switchport trunk allowed vlan {tv}
   no shutdown
!
interface Ethernet50
   description To campus-dist1 Eth49
   switchport mode trunk
   switchport trunk allowed vlan {tv}
   no shutdown
!
interface Ethernet51
   description To campus-dist2 Eth49
   switchport mode trunk
   switchport trunk allowed vlan {tv}
   no shutdown
!
spanning-tree mode rapid-pvst
spanning-tree priority 4096
!
end
"""
    return cfg

# ── Distribution 1 ────────────────────────────────────────────────────────────
def gen_dist1():
    vlans = [100,200,300,400,500,800,1000,1100,1800,1900]
    tv    = trunk_vlans(vlans)
    cfg   = mgmt_header("campus-dist1", "10.200.0.32")
    cfg  += vlan_block(vlans)
    cfg  += f"""interface Ethernet49
   description To campus-core Eth50
   switchport mode trunk
   switchport trunk allowed vlan {trunk_vlans(ALL_VLANS)}
   no shutdown
!
interface Ethernet50
   description To campus-access1 Eth49
   switchport mode trunk
   switchport trunk allowed vlan {trunk_vlans([100,200,400,800,1000,1100,1800,1900])}
   no shutdown
!
interface Ethernet51
   description To campus-access2 Eth49
   switchport mode trunk
   switchport trunk allowed vlan {trunk_vlans([300,500,800,1100,1800])}
   no shutdown
!
spanning-tree mode rapid-pvst
spanning-tree vlan {tv} priority 8192
!
end
"""
    return cfg

# ── Distribution 2 ────────────────────────────────────────────────────────────
def gen_dist2():
    vlans = [600,700,900,1100,1200,1300,1400,1500,1600,1700,1800]
    tv    = trunk_vlans(vlans)
    cfg   = mgmt_header("campus-dist2", "10.200.0.33")
    cfg  += vlan_block(vlans)
    cfg  += f"""interface Ethernet49
   description To campus-core Eth51
   switchport mode trunk
   switchport trunk allowed vlan {trunk_vlans(ALL_VLANS)}
   no shutdown
!
interface Ethernet50
   description To campus-access3 Eth49
   switchport mode trunk
   switchport trunk allowed vlan {trunk_vlans([600,700,900,1100,1800,800])}
   no shutdown
!
interface Ethernet51
   description To campus-access4 Eth49
   switchport mode trunk
   switchport trunk allowed vlan {trunk_vlans([1200,1400,1500,1600,1700,1300,1100,1800])}
   no shutdown
!
spanning-tree mode rapid-pvst
spanning-tree vlan {tv} priority 8192
!
end
"""
    return cfg

# ── Access switch helper ───────────────────────────────────────────────────────
def access_ports(port_map):
    """Generate access port configs with descriptive labels."""
    lines = []
    for eth, (label, vlan) in sorted(port_map.items()):
        lines.append(f"interface Ethernet{eth}")
        lines.append(f"   description {label}")
        lines.append(f"   switchport access vlan {vlan}")
        lines.append(f"   spanning-tree portfast")
        lines.append(f"   no shutdown")
        lines.append("!")
    return "\n".join(lines)

# ── Access 1: Finance / HR / WiFi / CCTV / Phone / Printer ───────────────────
def gen_access1():
    vlans    = [100,200,400,800,1000,1100,1800,1900]
    port_map = {
        **{i: (f"Finance-Workstation-{i:02d}", 100) for i in range(1,13)},
        **{i: (f"HR-Workstation-{i-12:02d}",   200) for i in range(13,21)},
        21: ("WiFi-AP-Corp-1",   1800), 22: ("WiFi-AP-Corp-2",  1800),
        23: ("WiFi-AP-Corp-3",   1800), 24: ("WiFi-AP-Corp-4",  1800),
        25: ("WiFi-AP-Guest-1",  1900), 26: ("WiFi-AP-Guest-2", 1900),
        **{i: (f"IP-Phone-{i-28:02d}", 800)  for i in range(29,37)},
        37: ("CCTV-Camera-Lobby-1",   1100), 38: ("CCTV-Camera-Lobby-2",   1100),
        39: ("CCTV-Camera-Corridor-1",1100), 40: ("CCTV-Camera-Corridor-2",1100),
        41: ("Printer-Color-1",  1000), 42: ("Printer-BW-1",     1000),
        43: ("Printer-BW-2",     1000), 44: ("Printer-MFP-1",    1000),
        45: ("Guest-Kiosk-1",    400),  46: ("Guest-Kiosk-2",    400),
        47: ("Guest-Kiosk-3",    400),  48: ("Guest-Kiosk-4",    400),
    }
    cfg  = mgmt_header("campus-access1", "10.200.0.41")
    cfg += vlan_block(vlans)
    cfg += f"""interface Ethernet49
   description Uplink to campus-dist1
   switchport mode trunk
   switchport trunk allowed vlan {trunk_vlans(vlans)}
   no shutdown
!
"""
    cfg += access_ports(port_map)
    cfg += "\nend\n"
    return cfg

# ── Access 2: IoT / Dev / WiFi / CCTV / Phone / Badge ────────────────────────
def gen_access2():
    vlans    = [300,500,800,1100,1800]
    port_map = {
        **{i: (f"IoT-Sensor-{i:02d}",          300) for i in range(1,13)},
        **{i: (f"Dev-Workstation-{i-12:02d}",   500) for i in range(13,21)},
        21: ("WiFi-AP-Corp-5",  1800), 22: ("WiFi-AP-Corp-6",  1800),
        23: ("WiFi-AP-Corp-7",  1800), 24: ("WiFi-AP-Corp-8",  1800),
        25: ("WiFi-AP-Corp-9",  1800), 26: ("WiFi-AP-Corp-10", 1800),
        27: ("WiFi-AP-Corp-11", 1800), 28: ("WiFi-AP-Corp-12", 1800),
        **{i: (f"IP-Phone-Dev-{i-28:02d}", 800) for i in range(29,37)},
        37: ("CCTV-Camera-Parking-1",  1100), 38: ("CCTV-Camera-Parking-2",  1100),
        39: ("CCTV-Camera-Entrance-1", 1100), 40: ("CCTV-Camera-Entrance-2", 1100),
        41: ("CCTV-Camera-Loading-1",  1100), 42: ("CCTV-Camera-Loading-2",  1100),
        43: ("CCTV-Camera-Loading-3",  1100), 44: ("CCTV-Camera-Loading-4",  1100),
        45: ("Badge-Reader-Main-1",    300),  46: ("Badge-Reader-Main-2",    300),
        47: ("Badge-Reader-Server-1",  300),  48: ("Badge-Reader-Server-2",  300),
    }
    cfg  = mgmt_header("campus-access2", "10.200.0.42")
    cfg += vlan_block(vlans)
    cfg += f"""interface Ethernet49
   description Uplink to campus-dist1
   switchport mode trunk
   switchport trunk allowed vlan {trunk_vlans(vlans)}
   no shutdown
!
"""
    cfg += access_ports(port_map)
    cfg += "\nend\n"
    return cfg

# ── Access 3: Prod / SCADA / UCM / CCTV / WiFi / Phone ───────────────────────
def gen_access3():
    vlans    = [600,700,800,900,1100,1800]
    port_map = {
        **{i: (f"Prod-Workstation-{i:02d}",     600) for i in range(1,13)},
        **{i: (f"SCADA-PLC-{i-12:02d}",         700) for i in range(13,21)},
        21: ("UCM-Server-Primary",   900), 22: ("UCM-Server-Secondary", 900),
        23: ("UCM-Server-Standby",   900), 24: ("UCM-TFTP-Server",      900),
        25: ("CCTV-Camera-Prod-1",  1100), 26: ("CCTV-Camera-Prod-2",  1100),
        27: ("CCTV-Camera-Prod-3",  1100), 28: ("CCTV-Camera-Prod-4",  1100),
        29: ("CCTV-Camera-SCADA-1", 1100), 30: ("CCTV-Camera-SCADA-2", 1100),
        31: ("CCTV-Camera-SCADA-3", 1100), 32: ("CCTV-Camera-SCADA-4", 1100),
        33: ("WiFi-AP-Prod-1",  1800), 34: ("WiFi-AP-Prod-2",  1800),
        35: ("WiFi-AP-Prod-3",  1800), 36: ("WiFi-AP-Prod-4",  1800),
        37: ("WiFi-AP-Prod-5",  1800), 38: ("WiFi-AP-Prod-6",  1800),
        39: ("WiFi-AP-Prod-7",  1800), 40: ("WiFi-AP-Prod-8",  1800),
        **{i: (f"IP-Phone-Prod-{i-40:02d}", 800) for i in range(41,49)},
    }
    cfg  = mgmt_header("campus-access3", "10.200.0.43")
    cfg += vlan_block(vlans)
    cfg += f"""interface Ethernet49
   description Uplink to campus-dist2
   switchport mode trunk
   switchport trunk allowed vlan {trunk_vlans(vlans)}
   no shutdown
!
"""
    cfg += access_ports(port_map)
    cfg += "\nend\n"
    return cfg

# ── Access 4: Billing / DB / App / Storage / Backup / Mgmt / CCTV / WiFi ─────
def gen_access4():
    vlans    = [1200,1300,1400,1500,1600,1700,1100,1800]
    port_map = {
        1:  ("Billing-Server-Primary",   1200), 2: ("Billing-Server-Secondary",1200),
        3:  ("Billing-Server-DR",        1200), 4: ("Billing-App-Server",       1200),
        5:  ("DB-Server-Primary",        1400), 6: ("DB-Server-Secondary",      1400),
        7:  ("DB-Server-Replica",        1400), 8: ("DB-Server-Analytics",      1400),
        9:  ("App-Server-Web-1",         1500),10: ("App-Server-Web-2",         1500),
        11: ("App-Server-API-1",         1500),12: ("App-Server-API-2",         1500),
        13: ("App-Server-Worker-1",      1500),14: ("App-Server-Worker-2",      1500),
        15: ("App-Server-Worker-3",      1500),16: ("App-Server-Cache",         1500),
        17: ("Storage-SAN-Controller-1", 1600),18: ("Storage-SAN-Controller-2", 1600),
        19: ("Storage-NAS-1",            1600),20: ("Storage-NAS-2",            1600),
        21: ("Backup-Server-Primary",    1700),22: ("Backup-Server-Secondary",  1700),
        23: ("Backup-Media-Server",      1700),24: ("Backup-Proxy-Server",      1700),
        25: ("Mgmt-Jump-Host-1",         1300),26: ("Mgmt-Jump-Host-2",         1300),
        27: ("Mgmt-Monitoring-Server",   1300),28: ("Mgmt-SIEM-Server",         1300),
        29: ("Mgmt-NTP-Server",          1300),30: ("Mgmt-DNS-Server",          1300),
        31: ("Mgmt-DHCP-Server",         1300),32: ("Mgmt-Syslog-Server",       1300),
        33: ("CCTV-Camera-DC-1",         1100),34: ("CCTV-Camera-DC-2",         1100),
        35: ("CCTV-Camera-DC-3",         1100),36: ("CCTV-Camera-DC-4",         1100),
        37: ("CCTV-NVR-Primary",         1100),38: ("CCTV-NVR-Secondary",       1100),
        39: ("CCTV-Camera-DC-5",         1100),40: ("CCTV-Camera-DC-6",         1100),
        41: ("WiFi-AP-DC-1",             1800),42: ("WiFi-AP-DC-2",             1800),
        43: ("WiFi-AP-DC-3",             1800),44: ("WiFi-AP-DC-4",             1800),
        45: ("WiFi-AP-DC-5",             1800),46: ("WiFi-AP-DC-6",             1800),
        47: ("WiFi-AP-DC-7",             1800),48: ("WiFi-AP-DC-8",             1800),
    }
    cfg  = mgmt_header("campus-access4", "10.200.0.44")
    cfg += vlan_block(vlans)
    cfg += f"""interface Ethernet49
   description Uplink to campus-dist2
   switchport mode trunk
   switchport trunk allowed vlan {trunk_vlans(vlans)}
   no shutdown
!
"""
    cfg += access_ports(port_map)
    cfg += "\nend\n"
    return cfg

# ── Generate all configs ───────────────────────────────────────────────────────
def main():
    print(f"Generating extended configs in {OUT}/")

    # Spines
    (OUT/"spine1.cfg").write_text(gen_spine(
        1,"10.200.0.11","10.255.0.1",65000,[
            ("10.0.0.1","65011","dc-leaf1","10.0.0.0"),
            ("10.0.0.3","65012","dc-leaf2","10.0.0.2"),
            ("10.0.0.5","65013","dc-leaf3","10.0.0.4"),
            ("10.0.0.7","65014","dc-leaf4","10.0.0.6"),
        ]))
    (OUT/"spine2.cfg").write_text(gen_spine(
        2,"10.200.0.12","10.255.0.2",65000,[
            ("10.0.0.9", "65011","dc-leaf1","10.0.0.8"),
            ("10.0.0.11","65012","dc-leaf2","10.0.0.10"),
            ("10.0.0.13","65013","dc-leaf3","10.0.0.12"),
            ("10.0.0.15","65014","dc-leaf4","10.0.0.14"),
        ]))
    print("  spine1.cfg, spine2.cfg")

    # Leaves
    (OUT/"leaf1.cfg").write_text(gen_leaf(1,"10.200.0.21","10.255.0.11",65011,
        "10.0.0.0","10.0.0.1","10.0.0.8","10.0.0.9",[100,200,400,500]))
    (OUT/"leaf2.cfg").write_text(gen_leaf(2,"10.200.0.22","10.255.0.12",65012,
        "10.0.0.2","10.0.0.3","10.0.0.10","10.0.0.11",[300,600,700,800]))
    (OUT/"leaf3.cfg").write_text(gen_leaf(3,"10.200.0.23","10.255.0.13",65013,
        "10.0.0.4","10.0.0.5","10.0.0.12","10.0.0.13",[800,900,1100,1200]))
    (OUT/"leaf4.cfg").write_text(gen_leaf(4,"10.200.0.24","10.255.0.14",65014,
        "10.0.0.6","10.0.0.7","10.0.0.14","10.0.0.15",[400,600,1300]))
    print("  leaf1-4.cfg")

    # Campus
    (OUT/"campus_core.cfg").write_text(gen_campus_core())
    (OUT/"dist1.cfg").write_text(gen_dist1())
    (OUT/"dist2.cfg").write_text(gen_dist2())
    print("  campus_core.cfg, dist1.cfg, dist2.cfg")

    # Access switches with port labels
    (OUT/"access1.cfg").write_text(gen_access1())
    (OUT/"access2.cfg").write_text(gen_access2())
    (OUT/"access3.cfg").write_text(gen_access3())
    (OUT/"access4.cfg").write_text(gen_access4())
    print("  access1-4.cfg (with WiFi/CCTV/Server/Phone labels)")

    print(f"\nDone. {len(list(OUT.glob('*.cfg')))} configs.")
    print("\nPort label summary:")
    print("  access1: Finance WS, HR WS, WiFi AP (Corp+Guest), IP Phones, CCTV, Printers, Guest Kiosks")
    print("  access2: IoT Sensors, Dev WS, WiFi APs, IP Phones, CCTV (Parking/Entrance), Badge Readers")
    print("  access3: Prod WS, SCADA PLCs, UCM Servers, CCTV (Prod/SCADA), WiFi APs, IP Phones")
    print("  access4: Billing/DB/App/Storage/Backup Servers, Mgmt, CCTV (DC), WiFi APs")
    print("\nNew VLANs added:")
    print("  VLAN 1400 - DB-Servers   (10.10.14.0/24)")
    print("  VLAN 1500 - App-Servers  (10.10.15.0/24)")
    print("  VLAN 1600 - Storage      (10.10.16.0/24)")
    print("  VLAN 1700 - Backup       (10.10.17.0/24)")
    print("  VLAN 1800 - WiFi-Corp    (10.10.18.0/24)")
    print("  VLAN 1900 - WiFi-Guest   (10.10.19.0/24)")


if __name__ == "__main__":
    main()
