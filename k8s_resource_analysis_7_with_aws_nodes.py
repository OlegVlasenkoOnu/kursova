import re
import os
import csv # Додано для роботи з CSV
import math # Додано для math.ceil
from typing import Dict, Any, List, Optional, Tuple

# Глобальна структура для зберігання даних кластера
cluster_data: Dict[str, Any] = {
    "nodes": {},
    "pods": {}
}
TARGET_NAMESPACE = "xenia-stg"
EC2_INSTANCE_DATA_FILE = "Amazon EC2 Instance Comparison.csv" # Шлях до файлу з даними EC2

# Порогові значення (глобальні константи)
CPU_REQ_HIGH_THRESHOLD = 85.0  # %
MEM_REQ_HIGH_THRESHOLD = 85.0  # %
POD_REQ_HIGH_THRESHOLD = 90.0  # %
CPU_LIMIT_OVERCOMMIT_WARN_THRESHOLD = 150.0 # %
CPU_LIMIT_OVERCOMMIT_CRITICAL_THRESHOLD = 300.0 # %
MEM_LIMIT_OVERCOMMIT_WARN_THRESHOLD = 95.0 # %
MEM_LIMIT_OVERCOMMIT_CRITICAL_THRESHOLD = 100.0 # % (не можна перевищувати allocatable)
POD_HIGH_RESOURCE_REQUEST_THRESHOLD = 50.0 # % від allocatable вузла для одного поду
USAGE_VS_REQUEST_LOW_RATIO = 0.3 
USAGE_VS_REQUEST_HIGH_RATIO = 1.5 
USAGE_VS_LIMIT_HIGH_THRESHOLD = 90.0 
NODE_LOW_UTILIZATION_THRESHOLD = 20.0 # % (CPU або Mem)
NODE_HIGH_UTILIZATION_THRESHOLD = 85.0 # % (CPU або Mem)


# --- Функції для конвертації ресурсів ---
def parse_cpu(cpu_str: Optional[str]) -> float:
    if not cpu_str or cpu_str == '0' or isinstance(cpu_str, str) and cpu_str.startswith('---'):
        return 0.0
    cpu_str = str(cpu_str).lower().strip()
    if 'm' in cpu_str:
        try: return float(cpu_str.replace('m', ''))
        except ValueError:
            match = re.match(r"(\d+)m", cpu_str)
            if match: return float(match.group(1))
            return 0.0
    try: return float(cpu_str) * 1000
    except ValueError:
        return 0.0

def parse_memory(mem_str: Optional[str]) -> float:
    if not mem_str or isinstance(mem_str, str) and mem_str.startswith('---'):
        return 0.0
    mem_str = str(mem_str).lower().strip()
    mem_str = re.sub(r'\s*\([^)]*\)', '', mem_str) 
    value_str = re.sub(r'[a-z]+$', '', mem_str)
    unit = mem_str.replace(value_str, '')
    try:
        value = float(value_str)
        if unit == 'gi': return value * 1024
        elif unit == 'g': return value * (1000**3) / (1024**2)
        elif unit == 'mi': return value
        elif unit == 'm': return value * (1000**2) / (1024**2)
        elif unit == 'ki': return value / 1024
        elif unit == 'k': return value * 1000 / (1024**2)
        elif not unit and value > 0 : return value / (1024**2) 
        elif not unit and value == 0: return 0.0
        else:
            return 0.0
    except ValueError:
        return 0.0

def format_cpu(cpu_millicores: float) -> str:
    if cpu_millicores >= 1000: return f"{cpu_millicores / 1000:.2f} cores"
    return f"{cpu_millicores:.0f}m"

def format_memory(mem_mib: float) -> str:
    if mem_mib == 0: return "0 MiB"
    if abs(mem_mib) >= 1024: return f"{mem_mib / 1024:.2f} GiB"
    return f"{mem_mib:.2f} MiB"

# --- Функція для парсингу даних EC2 інстансів ---
def parse_ec2_instance_data(file_path: str) -> List[Dict[str, Any]]:
    """
    Парсить CSV файл з даними про EC2 інстанси.
    Очікувані стовпці: "API Name", "vCPUs", "Instance Memory", "On Demand"
    """
    instances = []
    try:
        with open(file_path, mode='r', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            api_name_col = "API Name"
            vcpu_col = "vCPUs"
            memory_col = "Instance Memory"
            price_col = "On Demand"

            if not reader.fieldnames:
                print(f"Помилка: Не вдалося прочитати заголовки стовпців з файлу '{file_path}'. Файл може бути порожнім або мати некоректний формат CSV.")
                return []

            if not all(col in reader.fieldnames for col in [api_name_col, vcpu_col, memory_col, price_col]):
                print(f"Помилка: CSV файл '{file_path}' не містить всіх необхідних стовпців: '{api_name_col}', '{vcpu_col}', '{memory_col}', '{price_col}'. Наявні стовпці: {reader.fieldnames}")
                return []

            for row in reader:
                try:
                    api_name = row[api_name_col].strip()
                    
                    vcpu_val_str = row[vcpu_col].strip()
                    if not vcpu_val_str:
                        continue
                    
                    vcpu_match = re.match(r"(\d+\.?\d*)", vcpu_val_str)
                    if not vcpu_match:
                        continue
                    vcpu = float(vcpu_match.group(1)) * 1000

                    memory_gib_val = row[memory_col].strip()
                    if not memory_gib_val:
                        continue
                    memory_gib_val = memory_gib_val.lower().replace("gib", "").replace("gi", "").strip()
                    memory_mib = float(memory_gib_val) * 1024
                    
                    price_str_full = row[price_col].strip()
                    if not price_str_full:
                        continue
                    
                    if price_str_full.lower() == 'unavailable':
                        # print(f"Попередження: Ціна для інстансу '{api_name}' вказана як 'unavailable', інстанс пропускається.")
                        continue
                    
                    price_str_cleaned = price_str_full.replace('$', '')
                    price_match = re.match(r"(\d+\.?\d*)", price_str_cleaned)
                    if not price_match:
                        continue
                    price_per_hour = float(price_match.group(1))


                    if vcpu > 0 and memory_mib > 0 and price_per_hour > 0:
                        instances.append({
                            "name": api_name,
                            "cpu_millicores": vcpu,
                            "memory_mib": memory_mib,
                            "price_per_hour": price_per_hour
                        })
                except ValueError as e:
                    print(f"Попередження: Не вдалося обробити рядок для інстансу (можливо '{row.get(api_name_col, 'N/A')}') зі значеннями: vCPU='{row.get(vcpu_col)}', Mem='{row.get(memory_col)}', Price='{row.get(price_col)}'. Помилка: {e}")
                except Exception as e:
                    print(f"Помилка обробки рядка в CSV: {row}. Помилка: {e}")
    except FileNotFoundError:
        print(f"Помилка: Файл з даними EC2 інстансів '{file_path}' не знайдено.")
    except Exception as e:
        print(f"Помилка читання файлу EC2 інстансів '{file_path}': {e}")
    
    if instances:
        print(f"Успішно завантажено дані для {len(instances)} типів EC2 інстансів з '{file_path}'.")
    return instances

# --- Функції парсингу Kubernetes ---
def parse_get_pods(output: str, namespace_filter: Optional[str] = None):
    if namespace_filter: print(f"\n--- Парсинг: kubectl get pods -n {namespace_filter} -o wide ---")
    else: print(f"\n--- Парсинг: kubectl get pods -A -o wide ---")
    lines = output.strip().split('\n')
    if not lines: print("Помилка: Порожній вивід для get pods."); return

    header_line_index = -1
    for i, line_content in enumerate(lines):
        if "NAME" in line_content and "STATUS" in line_content and "NODE" in line_content:
            header_line_index = i; break
    if header_line_index == -1:
        if lines and lines[0].lower().startswith("name"): header_line_index = 0
        elif len(lines) > 1 and lines[1].lower().startswith("name"): header_line_index = 1
    if header_line_index == -1: print("Помилка: Не знайдено рядок заголовка у виводі get pods."); return
    
    header_line = lines[header_line_index]
    data_lines = lines[header_line_index+1:]
    headers = re.split(r'\s{2,}', header_line.strip())
    try:
        headers_lower = [h.lower() for h in headers]
        name_idx, status_idx, ip_idx, node_idx = headers_lower.index('name'), headers_lower.index('status'), headers_lower.index('ip'), headers_lower.index('node')
        ns_idx = headers_lower.index("namespace") if "namespace" in headers_lower else -1
    except ValueError as e: print(f"Помилка: Не знайдено колонку: {e} в {header_line.strip()} ({headers})"); return

    parsed_pods_list = []
    for line in data_lines:
        parts = re.split(r'\s{2,}', line.strip())
        current_namespace = namespace_filter
        if ns_idx != -1 and len(parts) > ns_idx: current_namespace = parts[ns_idx]
        if not current_namespace: 
            if line.strip(): print(f"Попередження: Не визначено неймспейс для: {line.strip()}"); 
            continue
        if len(parts) <= max(name_idx, status_idx, ip_idx, node_idx):
            if line.strip() and not line.lower().startswith("no resources found"): print(f"Попередження: Не розпарсено рядок get pods: {line.strip()}");
            continue

        pod_name, status = parts[name_idx], parts[status_idx]
        ip = parts[ip_idx] if parts[ip_idx] != '<none>' else None
        node_name_val = parts[node_idx] if parts[node_idx] != '<none>' else "N/A"
        if pod_name == "-----------": continue

        pod_info = cluster_data["pods"].setdefault(current_namespace, {}).setdefault(pod_name, {})
        pod_info.update({"name": pod_name, "status": status.strip(), "ip": ip, "node": node_name_val, "namespace": current_namespace}) 
        if "containers" not in pod_info: pod_info["containers"] = {}
        if "usage" not in pod_info: pod_info["usage"] = {}
        parsed_pods_list.append(pod_info)
        if node_name_val != "N/A":
            node_entry = cluster_data["nodes"].setdefault(node_name_val, {
                "capacity": {}, "allocatable": {}, "usage": {},
                "non_terminated_pods_list": [],
                "allocated_from_node": {"requests": {}, "limits": {}},
                "pods_on_node": {}
            })
            node_entry.setdefault("pods_on_node", {})[f"{current_namespace}/{pod_name}"] = {"status": status.strip()} 
    print("Знайдено або оновлено поди:")
    for pod in parsed_pods_list: print(f"  - {pod.get('namespace', namespace_filter)}/{pod['name']} (Статус: {pod['status']}, Вузол: {pod['node']}, IP: {pod['ip'] or 'N/A'})")


def parse_describe_node(output: str):
    print(f"\n--- Парсинг: kubectl describe node ---")
    node_name_from_file = None
    
    current_capacity_parsed: Dict[str, Any] = {}
    current_allocatable_parsed: Dict[str, Any] = {}
    current_allocated_requests_parsed: Dict[str, Any] = {}
    current_allocated_limits_parsed: Dict[str, Any] = {}
    current_non_terminated_pods_list_parsed: List[Dict[str, Any]] = []

    name_re = re.compile(r"^Name:\s+(\S+)")
    resource_re = re.compile(r"^\s*(cpu|memory|pods|ephemeral-storage):\s*(\S+)")
    allocated_line_re = re.compile(r"^\s+(cpu|memory|pods|ephemeral-storage)\s+([0-9a-zA-Z\.\-]+(?:m|Ki|Mi|Gi|Ti|Pi|Ei)?)(?:\s*\(.*?\))?(?:\s+([0-9a-zA-Z\.\-]+(?:m|Ki|Mi|Gi|Ti|Pi|Ei)?)(?:\s*\(.*?\))?)?")
    pod_line_re = re.compile(r"^\s+([^\s]+)\s+([^\s]+)\s+([0-9a-zA-Z\.\(\)\-%KiMiGi]+)\s+([0-9a-zA-Z\.\(\)\-%KiMiGi]+)\s+([0-9a-zA-Z\.\(\)\-%KiMiGi]+)\s+([0-9a-zA-Z\.\(\)\-%KiMiGi]+)")

    active_section = None
    lines = output.strip().split('\n')

    def store_parsed_node_data(target_node_name):
        if not target_node_name: return
        node_global_data = cluster_data["nodes"].setdefault(target_node_name, {
            "capacity": {}, "allocatable": {}, "usage": {},
            "non_terminated_pods_list": [],
            "allocated_from_node": {"requests": {}, "limits": {}},
            "pods_on_node": {}
        })
        node_global_data.setdefault("capacity", {}).update(current_capacity_parsed)
        node_global_data.setdefault("allocatable", {}).update(current_allocatable_parsed)
        node_global_data.setdefault("allocated_from_node", {}).setdefault("requests", {}).update(current_allocated_requests_parsed)
        node_global_data.setdefault("allocated_from_node", {}).setdefault("limits", {}).update(current_allocated_limits_parsed)
        if current_non_terminated_pods_list_parsed: 
            node_global_data["non_terminated_pods_list"] = current_non_terminated_pods_list_parsed
        
        print("Інформація про вузол:")
        print(f"  - Ім'я: {target_node_name}")
        cap_print = node_global_data.get("capacity", {})
        alloc_print = node_global_data.get("allocatable", {})
        alloc_from_node_req_print = node_global_data.get("allocated_from_node", {}).get("requests", {})
        alloc_from_node_lim_print = node_global_data.get("allocated_from_node", {}).get("limits", {})
        print(f"  - Ємність (Capacity): CPU={format_cpu(cap_print.get('cpu',0))}, Пам'ять={format_memory(cap_print.get('memory',0))}, Поди={int(cap_print.get('pods',0))}")
        print(f"  - Доступно (Allocatable): CPU={format_cpu(alloc_print.get('cpu',0))}, Пам'ять={format_memory(alloc_print.get('memory',0))}, Поди={int(alloc_print.get('pods',0))}")
        print(f"  - Знайдено Non-terminated Pods: {len(node_global_data.get('non_terminated_pods_list',[]))} шт.")
        print(f"  - Виділено (з секції Allocated resources): Requests(CPU={format_cpu(alloc_from_node_req_print.get('cpu', 0))}, Mem={format_memory(alloc_from_node_req_print.get('memory', 0))}, Pods={int(alloc_from_node_req_print.get('pods',0))}), Limits(CPU={format_cpu(alloc_from_node_lim_print.get('cpu', 0))}, Mem={format_memory(alloc_from_node_lim_print.get('memory', 0))})")

    for line in lines:
        name_match = name_re.match(line)
        if name_match:
            if node_name_from_file: store_parsed_node_data(node_name_from_file)
            node_name_from_file = name_match.group(1)
            print(f"Обробка вузла: {node_name_from_file}")
            current_capacity_parsed, current_allocatable_parsed, current_allocated_requests_parsed, current_allocated_limits_parsed, current_non_terminated_pods_list_parsed = {}, {}, {}, {}, []
            active_section = None
            cluster_data["nodes"].setdefault(node_name_from_file, {
                "capacity": {}, "allocatable": {}, "usage": {},
                "non_terminated_pods_list": [],
                "allocated_from_node": {"requests": {}, "limits": {}},
                "pods_on_node": {}
            })
            continue

        if not node_name_from_file: continue
        stripped_line = line.strip()
        if stripped_line == "Capacity:": active_section = "capacity"; continue
        if stripped_line == "Allocatable:": active_section = "allocatable"; continue
        if stripped_line == "Allocated resources:": active_section = "allocated_from_node"; continue
        if line.startswith("Non-terminated Pods:"): active_section = "non_terminated_pods"; current_non_terminated_pods_list_parsed = []; continue
        if stripped_line == "System Info:": active_section = "system_info"; continue
        if line.startswith("Events:"): active_section = "events"; continue
        if active_section and not line.startswith(" ") and not line.startswith("\t") and \
           not any(stripped_line.startswith(s_mark) for s_mark in ["Capacity:", "Allocatable:", "Allocated resources:", "Non-terminated Pods:", "System Info:", "Events:"]):
            active_section = None
        
        if active_section == "capacity":
            if match := resource_re.match(line.strip()):
                res_type, res_val = match.groups()
                if res_type == "cpu": current_capacity_parsed["cpu"] = parse_cpu(res_val)
                elif res_type == "memory": current_capacity_parsed["memory"] = parse_memory(res_val)
                elif res_type == "pods": current_capacity_parsed["pods"] = float(res_val) if res_val and res_val.replace('.', '', 1).isdigit() else 0.0
        elif active_section == "allocatable":
            if match := resource_re.match(line.strip()):
                res_type, res_val = match.groups()
                if res_type == "cpu": current_allocatable_parsed["cpu"] = parse_cpu(res_val)
                elif res_type == "memory": current_allocatable_parsed["memory"] = parse_memory(res_val)
                elif res_type == "pods": current_allocatable_parsed["pods"] = float(res_val) if res_val and res_val.replace('.', '', 1).isdigit() else 0.0
        elif active_section == "allocated_from_node":
            if "Resource" in line and "Requests" in line and "Limits" in line : continue
            if "--------" in line: continue
            if match := allocated_line_re.match(line):
                groups = match.groups()
                res_type, req_val_str = groups[0], groups[1]
                lim_val_str = groups[3] if len(groups) > 3 and groups[3] else None
                if res_type == "pods": current_allocated_requests_parsed["pods"] = float(req_val_str) if req_val_str and req_val_str.replace('.', '', 1).isdigit() else 0.0
                elif res_type not in ["ephemeral-storage"] and not res_type.startswith("hugepages"):
                    if req_val_str: current_allocated_requests_parsed[res_type] = parse_cpu(req_val_str) if res_type == "cpu" else parse_memory(req_val_str)
                    if lim_val_str: current_allocated_limits_parsed[res_type] = parse_cpu(lim_val_str) if res_type == "cpu" else parse_memory(lim_val_str)
        elif active_section == "non_terminated_pods":
            if "Namespace" in line and "CPU Requests" in line: continue
            if match := pod_line_re.match(line):
                ns, name, cpu_req_str, cpu_lim_str, mem_req_str, mem_lim_str = match.groups()
                if name == "----" or name == "Name": continue
                pod_details = {
                    "namespace": ns, "name": name,
                    "requests": {"cpu": parse_cpu(re.sub(r'\s*\(.+?\)', '', cpu_req_str)), "memory": parse_memory(re.sub(r'\s*\(.+?\)', '', mem_req_str))},
                    "limits": {"cpu": parse_cpu(re.sub(r'\s*\(.+?\)', '', cpu_lim_str)), "memory": parse_memory(re.sub(r'\s*\(.+?\)', '', mem_lim_str))}
                }
                current_non_terminated_pods_list_parsed.append(pod_details)
                pod_global_entry = cluster_data["pods"].setdefault(ns, {}).setdefault(name, {})
                if not pod_global_entry.get("node"): pod_global_entry["node"] = node_name_from_file
                if not pod_global_entry.get("containers"):
                     pod_global_entry["containers"] = { name: {"requests": pod_details["requests"], "limits": pod_details["limits"]}}
    
    store_parsed_node_data(node_name_from_file)
    if not node_name_from_file and output.strip():
        print("Помилка: Не вдалося визначити ім'я вузла з виводу describe node.")


def parse_describe_pod(output: str):
    print(f"\n--- Парсинг: kubectl describe pod ---")
    pod_name: Optional[str] = None
    namespace: Optional[str] = None
    node_name: Optional[str] = None
    status: Optional[str] = None
    qos_class: Optional[str] = None
    containers: Dict[str, Dict[str, Any]] = {} 
    events: List[str] = []

    name_re = re.compile(r"^Name:\s+(\S+)")
    namespace_re = re.compile(r"^Namespace:\s+(\S+)")
    node_re = re.compile(r"^Node:\s+(\S+?)(?:/|$)") 
    status_re = re.compile(r"^Status:\s+(\S+)")
    qos_re = re.compile(r"^QoS Class:\s+(\S+)")
    container_section_re = re.compile(r"^(Init C|C)ontainers:(?! Ready)") 
    container_name_re = re.compile(r"^\s\s(\S+):") 
    requests_re = re.compile(r"^\s+Requests:")
    limits_re = re.compile(r"^\s+Limits:")
    resource_re = re.compile(r"^\s+(cpu|memory):\s+(\S+)")
    events_section_re = re.compile(r"^Events:")

    current_container: Optional[str] = None
    in_container_section = False
    resource_section: Optional[str] = None 
    in_events_section = False

    lines = output.strip().split('\n')
    for i, line in enumerate(lines):
        if match := name_re.match(line): pod_name = match.group(1); continue
        if match := namespace_re.match(line): namespace = match.group(1); continue
        if match := node_re.match(line): node_name = match.group(1); continue
        if match := status_re.match(line): status = match.group(1); continue
        if match := qos_re.match(line): qos_class = match.group(1); continue
        
        if events_section_re.match(line):
            in_events_section = True; in_container_section = False 
            current_container = None; resource_section = None
            continue
        
        if in_events_section:
            if line.strip() and not line.startswith(" ") and not line.startswith("\t") and not line.startswith("Type"):
                in_events_section = False
            elif line.strip() and len(events) < 10 and not (line.strip().startswith("Type") and "Reason" in line.strip()):
                events.append(line.strip())
            if not in_events_section: pass
            else: continue

        if container_section_re.match(line):
            in_container_section = True; current_container = None; resource_section = None
            continue
        
        if in_container_section and not (line.startswith("  ") or line.startswith("\t")) and ":" in line and not (requests_re.match(line) or limits_re.match(line) or resource_re.match(line)):
            in_container_section = False; current_container = None; resource_section = None
            
        if in_container_section:
            if line.startswith("  ") and not line.startswith("    ") and line.strip().endswith(":"):
                candidate = line.strip()[:-1].strip()
                if ' ' not in candidate and (i + 1 < len(lines)) and \
                   (lines[i+1].strip().startswith("Container ID:") or \
                    lines[i+1].strip().startswith("Image:") or \
                    lines[i+1].strip().startswith("Ports:") or \
                    lines[i+1].strip().startswith("Host Ports:") or \
                    lines[i+1].strip().startswith("Command:") or \
                    lines[i+1].strip().startswith("State:") or \
                    lines[i+1].strip().startswith("Ready:") or \
                    lines[i+1].strip().startswith("Restart Count:") or \
                    lines[i+1].strip().startswith("Limits:") or \
                    lines[i+1].strip().startswith("Requests:") \
                    ):
                    current_container = candidate
                    containers.setdefault(current_container, {"requests": {}, "limits": {}, "name": current_container})
                    resource_section = None 
                    continue

            if current_container:
                if requests_re.match(line): resource_section = "requests"; continue
                elif limits_re.match(line): resource_section = "limits"; continue
                elif resource_section and (match := resource_re.match(line)):
                    res_type, res_value_str = match.groups()
                    value = parse_cpu(res_value_str) if res_type == "cpu" else parse_memory(res_value_str)
                    if value >= 0: 
                        containers[current_container].setdefault(resource_section, {})[res_type] = value

    if pod_name and namespace:
        pod_entry = cluster_data["pods"].setdefault(namespace, {}).setdefault(pod_name, {})
        pod_entry.update({
            "name": pod_name, "node": node_name,
            "status": (status.strip() if status else None) or pod_entry.get("status", "Unknown"), 
            "qos_class": qos_class, "events": events[-5:], "namespace": namespace 
        })
        if containers: pod_entry["containers"] = containers
        elif "containers" not in pod_entry: pod_entry["containers"] = {}
        if "usage" not in pod_entry: pod_entry["usage"] = {}

        print(f"Інформація про под: {namespace}/{pod_name} (Статус: {pod_entry['status']}, Вузол: {node_name or 'N/A'}, QoS: {qos_class or 'N/A'})")
        pod_containers = pod_entry.get("containers", {})
        if pod_containers:
            for c_name, c_data in pod_containers.items():
                req_cpu_val = c_data.get('requests', {}).get('cpu', 0.0)
                req_mem_val = c_data.get('requests', {}).get('memory', 0.0)
                lim_cpu_val = c_data.get('limits', {}).get('cpu', 0.0)
                lim_mem_val = c_data.get('limits', {}).get('memory', 0.0)
                print(f"  - Контейнер: {c_name}")
                print(f"    - Запити (Requests): CPU={format_cpu(req_cpu_val)}, Пам'ять={format_memory(req_mem_val)}")
                print(f"    - Ліміти (Limits): CPU={format_cpu(lim_cpu_val)}, Пам'ять={format_memory(lim_mem_val)}")
        else: print("  - Інформація про ресурси контейнерів не знайдена або не розпарсена в цьому файлі.")
        if pod_entry.get("events"):
            print("  - Останні події:")
            for event in pod_entry["events"]: print(f"    {event}")
    elif output.strip():
        print("Помилка: Не вдалося визначити ім'я пода або неймспейс з виводу describe pod.")


def parse_top_nodes(output: str):
    print(f"\n--- Парсинг: kubectl top node ---")
    lines = output.strip().split('\n')
    if len(lines) < 2: print("Помилка: Недостатньо рядків у виводі top node."); return
    header_line_index = 0
    if lines[0].lower().startswith("name"): header_line_index = 0
    elif len(lines) > 1 and lines[1].lower().startswith("name"): header_line_index = 1
    else: print("Помилка: Не знайдено заголовок в top node."); return

    data_lines = lines[header_line_index+1:]
    for line in data_lines:
        parts = line.split()
        if len(parts) >= 5: 
            node_name = parts[0]
            usage_cpu, usage_memory = parse_cpu(parts[1]), parse_memory(parts[3]) 
            node_data = cluster_data["nodes"].setdefault(node_name, {})
            node_data.setdefault("usage", {}).update({
                "cpu": usage_cpu, "memory": usage_memory,
                "cpu_percent_str": parts[2], "memory_percent_str": parts[4]
            })
            print(f"  Вузол: {node_name}, Використання CPU: {format_cpu(usage_cpu)} ({parts[2]}), Пам'ять: {format_memory(usage_memory)} ({parts[4]})")
        elif line.strip(): print(f"Попередження: Не вдалося розпарсити рядок у top node: {line.strip()}")

def parse_top_pods(output: str, namespace_filter: Optional[str] = None):
    if namespace_filter: print(f"\n--- Парсинг: kubectl top pod -n {namespace_filter} --containers ---")
    else: print(f"\n--- Парсинг: kubectl top pod -A --containers ---")
    lines = output.strip().split('\n')
    if len(lines) < 2: print("Помилка: Недостатньо рядків у виводі top pod."); return

    header_line_index = 0
    if lines[0].lower().startswith("pod"): header_line_index = 0 
    elif lines[0].lower().startswith("namespace") and "pod" in lines[0].lower(): header_line_index = 0 
    elif len(lines) > 1 and (lines[1].lower().startswith("pod") or (lines[1].lower().startswith("namespace") and "pod" in lines[1].lower())): header_line_index = 1
    else: print("Помилка: Не знайдено заголовок в top pod."); return
    
    data_lines = lines[header_line_index+1:]
    for line in data_lines:
        parts = line.split()
        current_pod_ns, pod_name_candidate, container_name, cpu_usage_str, mem_usage_str = None, None, None, None, None
        if not namespace_filter: 
            if len(parts) < 5:
                if line.strip(): print(f"Попередження: Неповний рядок у top pod -A: {line.strip()}");
                continue
            current_pod_ns, pod_name_candidate, container_name, cpu_usage_str, mem_usage_str = parts[0], parts[1], parts[2], parts[3], parts[4]
        else: 
            current_pod_ns = namespace_filter
            if len(parts) < 4:
                if line.strip(): print(f"Попередження: Неповний рядок у top pod -n: {line.strip()}");
                continue
            if not parts[0].startswith("CPU("): 
                pod_name_candidate, container_name, cpu_usage_str, mem_usage_str = parts[0], parts[1], parts[2], parts[3]
            else: 
                 if line.strip(): print(f"Попередження: Пропускається рядок у top pod -n: {line.strip()}");
                 continue
        if not current_pod_ns or not pod_name_candidate or not container_name:
            if line.strip(): print(f"Попередження: Не вдалося визначити дані для рядка top pod: {line.strip()}");
            continue
        usage_cpu, usage_memory = parse_cpu(cpu_usage_str), parse_memory(mem_usage_str)
        pod_entry = cluster_data["pods"].setdefault(current_pod_ns, {}).setdefault(pod_name_candidate, {})
        pod_entry.setdefault("usage", {}) 
        container_usage_entry = pod_entry.setdefault("containers_usage", {}).setdefault(container_name, {})
        container_usage_entry.update({"cpu": usage_cpu, "memory": usage_memory})
        print(f"  Под: {current_pod_ns}/{pod_name_candidate}, Контейнер: {container_name}, Використання CPU: {format_cpu(usage_cpu)}, Пам'ять: {format_memory(usage_memory)}")


def is_database(name: Optional[str]) -> bool: 
    if not name: return False
    name_lower = name.lower()
    db_keywords = ["mongo", "mysql", "postgres", "redis", "mariadb", "sql", "db", "database", "memcached", "cassandra", "elastic"]
    return any(keyword in name_lower for keyword in db_keywords)

def analyze_resources(step_name: str):
    print(f"\n--- Аналіз та Рекомендації ({step_name}) ---")

    if not cluster_data.get("nodes"):
        print("Немає даних про вузли для аналізу.")
        pending_pods = []
        for ns, pods_in_ns in cluster_data.get("pods", {}).items():
            for p_name, p_data in pods_in_ns.items():
                if p_data.get("status", "").strip().lower() == "pending" and not p_data.get("node"):
                    pending_pods.append(f"{ns}/{p_name}")
        if pending_pods:
            print(f"УВАГА: Є Pending поди без призначеного вузла: {', '.join(pending_pods)}")
            print("  РЕКОМЕНДАЦІЯ: Перевірте ресурси кластера, налаштування планувальника (scheduler), taints/tolerations, affinity rules та події (events) цих подів.")
        return

    cluster_totals = {
        "allocatable_cpu": 0.0, "allocatable_memory": 0.0, "allocatable_pods": 0.0,
        "requested_cpu": 0.0, "requested_memory": 0.0, "requested_pods": 0.0,
        "limits_cpu": 0.0, "limits_memory": 0.0,
        "usage_cpu": 0.0, "usage_memory": 0.0, 
    }
    all_node_analyses: Dict[str, Dict[str, Any]] = {}
    overall_active_pods_for_cluster_total = set() 

    print("\nАналіз по вузлах:")
    sorted_node_names = sorted(list(cluster_data["nodes"].keys()))

    for node_name in sorted_node_names:
        node_data = cluster_data["nodes"].get(node_name, {})
        allocatable = node_data.get("allocatable", {})
        alloc_cpu = allocatable.get("cpu", 0.0)
        alloc_mem = allocatable.get("memory", 0.0)
        alloc_pods_count = allocatable.get("pods", 0.0)

        if not (alloc_cpu > 0 and alloc_mem > 0 and alloc_pods_count > 0):
            continue
        
        print(f"\n- Вузол: {node_name}") 
        node_analysis: Dict[str, Any] = {
            "recommendations": [], "warnings": [], "errors": [], "info": [], "flags": [],
            "req_cpu_p": 0.0, "req_mem_p": 0.0 
        }
        all_node_analyses[node_name] = node_analysis 
        node_analysis["info"].append(f"Доступно (Allocatable): CPU={format_cpu(alloc_cpu)}, Пам'ять={format_memory(alloc_mem)}, Поди={int(alloc_pods_count)}")
        cluster_totals["allocatable_cpu"] += alloc_cpu
        cluster_totals["allocatable_memory"] += alloc_mem
        cluster_totals["allocatable_pods"] += alloc_pods_count

        node_usage = node_data.get("usage", {})
        usage_cpu_val = node_usage.get("cpu", 0.0)
        usage_mem_val = node_usage.get("memory", 0.0)
        if usage_cpu_val > 0 or usage_mem_val > 0:
            usage_cpu_perc = (usage_cpu_val / alloc_cpu * 100) if alloc_cpu > 0 else 0
            usage_mem_perc = (usage_mem_val / alloc_mem * 100) if alloc_mem > 0 else 0
            node_analysis["info"].append(f"Реальне використання (з top node): CPU={format_cpu(usage_cpu_val)} ({usage_cpu_perc:.1f}%), Пам'ять={format_memory(usage_mem_val)} ({usage_mem_perc:.1f}%)")
            cluster_totals["usage_cpu"] += usage_cpu_val
            cluster_totals["usage_memory"] += usage_mem_val
        
        current_node_requests = {"cpu": 0.0, "memory": 0.0, "pods": 0.0}
        current_node_limits = {"cpu": 0.0, "memory": 0.0}
        active_pods_on_node_details: List[str] = []
        target_namespace_pods_on_node: List[str] = []
        potential_db_pods_on_node: List[str] = []
        high_request_pods_on_node: List[str] = []
        pods_without_requests_on_node: List[str] = []
        pods_without_limits_on_node: List[str] = []
        
        aggregated_pods_on_node: Dict[str, Dict] = {} 
        for pod_desc_node in node_data.get("non_terminated_pods_list", []):
            ns = pod_desc_node.get("namespace")
            name = pod_desc_node.get("name")
            if not ns or not name or name == "----": continue
            full_pod_id = f"{ns}/{name}"
            aggregated_pods_on_node[full_pod_id] = {
                "requests": pod_desc_node.get("requests", {"cpu": 0, "memory": 0}),
                "limits": pod_desc_node.get("limits", {"cpu": 0, "memory": 0}),
                "status": "Unknown" 
            }
        
        for pod_ns_global, pods_in_ns_global in cluster_data.get("pods", {}).items():
            for pod_name_global, pod_data_global in pods_in_ns_global.items():
                if pod_data_global.get("node") == node_name:
                    full_pod_id = f"{pod_ns_global}/{pod_name_global}"
                    pod_total_requests_cpu, pod_total_requests_mem = 0.0, 0.0
                    pod_total_limits_cpu, pod_total_limits_mem = 0.0, 0.0
                    current_status = pod_data_global.get("status", "Unknown")
                    if pod_data_global.get("containers"): 
                        for c_data in pod_data_global["containers"].values():
                            pod_total_requests_cpu += c_data.get("requests", {}).get("cpu", 0.0)
                            pod_total_requests_mem += c_data.get("requests", {}).get("memory", 0.0)
                            pod_total_limits_cpu += c_data.get("limits", {}).get("cpu", 0.0)
                            pod_total_limits_mem += c_data.get("limits", {}).get("memory", 0.0)
                        aggregated_pods_on_node[full_pod_id] = {
                            "requests": {"cpu": pod_total_requests_cpu, "memory": pod_total_requests_mem},
                            "limits": {"cpu": pod_total_limits_cpu, "memory": pod_total_limits_mem},
                            "status": current_status
                        }
                    elif full_pod_id not in aggregated_pods_on_node: 
                        aggregated_pods_on_node[full_pod_id] = {
                             "requests": {"cpu": 0, "memory": 0}, "limits": {"cpu": 0, "memory": 0},
                             "status": current_status
                        }
                    else: 
                        aggregated_pods_on_node[full_pod_id]["status"] = current_status
        
        resource_consuming_statuses = ["Running", "Pending", "ContainerCreating", "PodInitializing", "Terminating"]
        for full_pod_id, agg_pod_data in aggregated_pods_on_node.items():
            ns, name = full_pod_id.split('/', 1)
            pod_status_original = agg_pod_data.get("status", "Unknown")
            pod_status_normalized = pod_status_original.strip().lower()
            if pod_status_normalized == "completed":
                continue 
            if ns == TARGET_NAMESPACE: target_namespace_pods_on_node.append(f"{name} ({pod_status_original})")
            if is_database(name): potential_db_pods_on_node.append(full_pod_id)
            if pod_status_original in resource_consuming_statuses:
                overall_active_pods_for_cluster_total.add(full_pod_id) 
                active_pods_on_node_details.append(f"{full_pod_id} ({pod_status_original})")
                current_node_requests["pods"] += 1
                req_cpu = agg_pod_data.get("requests",{}).get("cpu",0.0) 
                req_mem = agg_pod_data.get("requests",{}).get("memory",0.0)
                lim_cpu = agg_pod_data.get("limits",{}).get("cpu",0.0)
                lim_mem = agg_pod_data.get("limits",{}).get("memory",0.0)
                current_node_requests["cpu"] += req_cpu
                current_node_requests["memory"] += req_mem
                current_node_limits["cpu"] += lim_cpu
                current_node_limits["memory"] += lim_mem
                if req_cpu == 0 and req_mem == 0: pods_without_requests_on_node.append(full_pod_id)
                if lim_cpu == 0 and lim_mem == 0: pods_without_limits_on_node.append(full_pod_id)
                if alloc_cpu > 0 and (req_cpu / alloc_cpu * 100) > POD_HIGH_RESOURCE_REQUEST_THRESHOLD:
                    high_request_pods_on_node.append(f"{full_pod_id} (CPU: {format_cpu(req_cpu)} - {req_cpu / alloc_cpu * 100:.1f}%)")
                if alloc_mem > 0 and (req_mem / alloc_mem * 100) > POD_HIGH_RESOURCE_REQUEST_THRESHOLD:
                    high_request_pods_on_node.append(f"{full_pod_id} (Mem: {format_memory(req_mem)} - {req_mem / alloc_mem * 100:.1f}%)")
                pod_data_main_store = cluster_data["pods"].get(ns, {}).get(name, {})
                for c_name, c_usage_data in pod_data_main_store.get("containers_usage", {}).items():
                    c_main_data = pod_data_main_store.get("containers",{}).get(c_name,{})
                    c_req_cpu = c_main_data.get("requests",{}).get("cpu")
                    c_req_mem = c_main_data.get("requests",{}).get("memory")
                    c_lim_cpu = c_main_data.get("limits",{}).get("cpu")
                    c_lim_mem = c_main_data.get("limits",{}).get("memory")
                    c_usage_cpu = c_usage_data.get("cpu")
                    c_usage_mem = c_usage_data.get("memory")
                    if c_usage_cpu is not None and c_req_cpu is not None and c_req_cpu > 0:
                        if c_usage_cpu < c_req_cpu * USAGE_VS_REQUEST_LOW_RATIO: node_analysis["recommendations"].append(f"Контейнер {full_pod_id}/{c_name}: використання CPU ({format_cpu(c_usage_cpu)}) значно нижче запиту ({format_cpu(c_req_cpu)}). Розгляньте зменшення запиту CPU.")
                        elif c_usage_cpu > c_req_cpu * USAGE_VS_REQUEST_HIGH_RATIO: node_analysis["warnings"].append(f"Контейнер {full_pod_id}/{c_name}: використання CPU ({format_cpu(c_usage_cpu)}) значно вище запиту ({format_cpu(c_req_cpu)}). Розгляньте збільшення запиту CPU.")
                    if c_usage_mem is not None and c_req_mem is not None and c_req_mem > 0:
                        if c_usage_mem < c_req_mem * USAGE_VS_REQUEST_LOW_RATIO: node_analysis["recommendations"].append(f"Контейнер {full_pod_id}/{c_name}: використання Mem ({format_memory(c_usage_mem)}) значно нижче запиту ({format_memory(c_req_mem)}). Розгляньте зменшення запиту Mem.")
                        elif c_usage_mem > c_req_mem * USAGE_VS_REQUEST_HIGH_RATIO: node_analysis["warnings"].append(f"Контейнер {full_pod_id}/{c_name}: використання Mem ({format_memory(c_usage_mem)}) значно вище запиту ({format_memory(c_req_mem)}). Розгляньте збільшення запиту Mem.")
                    if c_usage_cpu is not None and c_lim_cpu is not None and c_lim_cpu > 0 and (c_usage_cpu / c_lim_cpu * 100) > USAGE_VS_LIMIT_HIGH_THRESHOLD: node_analysis["warnings"].append(f"Контейнер {full_pod_id}/{c_name}: використання CPU ({format_cpu(c_usage_cpu)}) близьке до ліміту ({format_cpu(c_lim_cpu)}). Ризик CPU throttling.")
                    if c_usage_mem is not None and c_lim_mem is not None and c_lim_mem > 0 and (c_usage_mem / c_lim_mem * 100) > USAGE_VS_LIMIT_HIGH_THRESHOLD: node_analysis["warnings"].append(f"Контейнер {full_pod_id}/{c_name}: використання Mem ({format_memory(c_usage_mem)}) близьке до ліміту ({format_memory(c_lim_mem)}). Ризик OOMKilled.")

        if active_pods_on_node_details: node_analysis["info"].append(f"Активні поди ({len(active_pods_on_node_details)}): {', '.join(sorted(list(set(active_pods_on_node_details))))}")
        if target_namespace_pods_on_node: node_analysis["info"].append(f"Поди з неймспейсу '{TARGET_NAMESPACE}': {', '.join(sorted(list(set(target_namespace_pods_on_node))))}")
        if potential_db_pods_on_node: node_analysis["info"].append(f"Потенційні БД: {', '.join(sorted(list(set(potential_db_pods_on_node))))}")
        if high_request_pods_on_node: node_analysis["warnings"].append(f"Поди з високими запитами (> {POD_HIGH_RESOURCE_REQUEST_THRESHOLD}%): {'; '.join(sorted(list(set(high_request_pods_on_node))))}. Перегляньте їх конфігурацію.")
        if pods_without_requests_on_node: node_analysis["warnings"].append(f"Поди БЕЗ ЗАПИТІВ (requests): {', '.join(sorted(list(set(pods_without_requests_on_node))))}. Це негативно впливає на планування та стабільність.")
        if pods_without_limits_on_node: node_analysis["warnings"].append(f"Поди БЕЗ ЛІМІТІВ (limits): {', '.join(sorted(list(set(pods_without_limits_on_node))))}. Це може призвести до неконтрольованого споживання ресурсів.")

        req_cpu_p = (current_node_requests["cpu"] / alloc_cpu * 100) if alloc_cpu > 0 else 0
        req_mem_p = (current_node_requests["memory"] / alloc_mem * 100) if alloc_mem > 0 else 0
        req_pods_p = (current_node_requests["pods"] / alloc_pods_count * 100) if alloc_pods_count > 0 else 0
        lim_cpu_p = (current_node_limits["cpu"] / alloc_cpu * 100) if alloc_cpu > 0 else 0
        lim_mem_p = (current_node_limits["memory"] / alloc_mem * 100) if alloc_mem > 0 else 0
        node_analysis["req_cpu_p"] = req_cpu_p 
        node_analysis["req_mem_p"] = req_mem_p
        node_analysis["info"].append(f"Сумарні Запити АКТИВНИХ (Requests): CPU={format_cpu(current_node_requests['cpu'])} ({req_cpu_p:.1f}%), Пам'ять={format_memory(current_node_requests['memory'])} ({req_mem_p:.1f}%), Поди={int(current_node_requests['pods'])} ({req_pods_p:.1f}%)")
        node_analysis["info"].append(f"Сумарні Ліміти АКТИВНИХ (Limits):   CPU={format_cpu(current_node_limits['cpu'])} ({lim_cpu_p:.1f}%), Пам'ять={format_memory(current_node_limits['memory'])} ({lim_mem_p:.1f}%)")
        unused_cpu_node = max(0, alloc_cpu - current_node_requests["cpu"])
        unused_mem_node = max(0, alloc_mem - current_node_requests["memory"])
        node_analysis["info"].append(f"Невикористані ресурси (Allocatable - Requested): CPU={format_cpu(unused_cpu_node)}, Пам'ять={format_memory(unused_mem_node)}")
        alloc_from_node_data = node_data.get("allocated_from_node", {})
        alloc_req_node_cpu = alloc_from_node_data.get("requests", {}).get("cpu")
        alloc_lim_node_cpu = alloc_from_node_data.get("limits", {}).get("cpu")
        alloc_req_node_mem = alloc_from_node_data.get("requests", {}).get("memory")
        alloc_lim_node_mem = alloc_from_node_data.get("limits", {}).get("memory")
        alloc_req_node_pods = alloc_from_node_data.get("requests", {}).get("pods")
        if alloc_req_node_cpu is not None: 
             node_analysis["info"].append(f"Дані з 'describe node' (Allocated): ReqCPU={format_cpu(alloc_req_node_cpu or 0)}, LimCPU={format_cpu(alloc_lim_node_cpu or 0)}, ReqMem={format_memory(alloc_req_node_mem or 0)}, LimMem={format_memory(alloc_lim_node_mem or 0)}, Pods={int(alloc_req_node_pods or 0)}")
             if alloc_lim_node_mem and alloc_mem > 0 and (alloc_lim_node_mem / alloc_mem * 100) > MEM_LIMIT_OVERCOMMIT_CRITICAL_THRESHOLD:
                 node_analysis["errors"].append(f"ЗАБОРОНЕНО: Memory Limits Overcommit ({alloc_lim_node_mem / alloc_mem * 100:.1f}%) згідно 'describe node'. Негайно виправте!")
             elif alloc_lim_node_mem and alloc_mem > 0 and (alloc_lim_node_mem / alloc_mem * 100) > MEM_LIMIT_OVERCOMMIT_WARN_THRESHOLD:
                 node_analysis["warnings"].append(f"Memory Limits Overcommit ({alloc_lim_node_mem / alloc_mem * 100:.1f}%) згідно 'describe node'. Високий ризик OOMKilled.")
        if req_cpu_p > CPU_REQ_HIGH_THRESHOLD: node_analysis["warnings"].append(f"Високе завантаження CPU за запитами ({req_cpu_p:.1f}%).")
        if req_mem_p > MEM_REQ_HIGH_THRESHOLD: node_analysis["warnings"].append(f"Високе завантаження Memory за запитами ({req_mem_p:.1f}%).")
        if req_pods_p > POD_REQ_HIGH_THRESHOLD: node_analysis["warnings"].append(f"Велика кількість подів ({req_pods_p:.1f}% від ліміту).")
        if lim_cpu_p > CPU_LIMIT_OVERCOMMIT_CRITICAL_THRESHOLD:
            node_analysis["errors"].append(f"КРИТИЧНО: CPU Limits Overcommit ({lim_cpu_p:.1f}%). Сильний CPU throttling неминучий.")
        elif lim_cpu_p > CPU_LIMIT_OVERCOMMIT_WARN_THRESHOLD:
            node_analysis["warnings"].append(f"CPU Limits Overcommit ({lim_cpu_p:.1f}%). Можливий CPU throttling.")
        if lim_mem_p > MEM_LIMIT_OVERCOMMIT_CRITICAL_THRESHOLD: 
            node_analysis["errors"].append(f"ЗАБОРОНЕНО: Memory Limits Overcommit ({lim_mem_p:.1f}%) по активних подах. Негайно виправте!")
        elif lim_mem_p > MEM_LIMIT_OVERCOMMIT_WARN_THRESHOLD:
            node_analysis["warnings"].append(f"Memory Limits Overcommit ({lim_mem_p:.1f}%) по активних подах. Підвищений ризик OOMKilled.")
        for item_type in ["info", "errors", "warnings", "recommendations", "flags"]:
            unique_items = sorted(list(set(node_analysis[item_type])))
            if unique_items:
                print(f"  {item_type.upper()}:")
                for item in unique_items:
                    print(f"    - {item}")
        if not any(node_analysis[k] for k in ["errors", "flags", "warnings", "recommendations"]): 
            print("  СТАН ВУЗЛА: OK (на основі запитів/лімітів)")
        cluster_totals["requested_cpu"] += current_node_requests["cpu"]
        cluster_totals["requested_memory"] += current_node_requests["memory"]
        cluster_totals["requested_pods"] += current_node_requests["pods"]
        cluster_totals["limits_cpu"] += current_node_limits["cpu"]
        cluster_totals["limits_memory"] += current_node_limits["memory"]

    cluster_totals["active_pods_count"] = len(overall_active_pods_for_cluster_total)
    print("\nЗагальний аналіз кластера:")
    if cluster_totals["allocatable_cpu"] > 0 and cluster_totals["allocatable_memory"] > 0:
        print(f"- Загально доступно (Allocatable): CPU={format_cpu(cluster_totals['allocatable_cpu'])}, Пам'ять={format_memory(cluster_totals['allocatable_memory'])}, Поди={int(cluster_totals['allocatable_pods'])}")
        print(f"- Загальна кількість активних подів (унікальних): {cluster_totals['active_pods_count']}")
        req_cpu_total_p = (cluster_totals["requested_cpu"] / cluster_totals["allocatable_cpu"] * 100) if cluster_totals["allocatable_cpu"] > 0 else 0
        req_mem_total_p = (cluster_totals["requested_memory"] / cluster_totals["allocatable_memory"] * 100) if cluster_totals["allocatable_memory"] > 0 else 0
        req_pod_total_p = (cluster_totals["requested_pods"] / cluster_totals["allocatable_pods"] * 100) if cluster_totals["allocatable_pods"] > 0 else 0
        print(f"- Загальні Запити (Requests): CPU={format_cpu(cluster_totals['requested_cpu'])} ({req_cpu_total_p:.1f}%), Пам'ять={format_memory(cluster_totals['requested_memory'])} ({req_mem_total_p:.1f}%), Поди (сума по вузлах)={int(cluster_totals['requested_pods'])} ({req_pod_total_p:.1f}%)")
        lim_cpu_total_p = (cluster_totals["limits_cpu"] / cluster_totals["allocatable_cpu"] * 100) if cluster_totals["allocatable_cpu"] > 0 else 0
        lim_mem_total_p = (cluster_totals["limits_memory"] / cluster_totals["allocatable_memory"] * 100) if cluster_totals["allocatable_memory"] > 0 else 0
        print(f"- Загальні Ліміти (Limits):   CPU={format_cpu(cluster_totals['limits_cpu'])} ({lim_cpu_total_p:.1f}%), Пам'ять={format_memory(cluster_totals['limits_memory'])} ({lim_mem_total_p:.1f}%)")
        unused_total_cpu = max(0, cluster_totals["allocatable_cpu"] - cluster_totals["requested_cpu"])
        unused_total_memory = max(0, cluster_totals["allocatable_memory"] - cluster_totals["requested_memory"])
        print(f"- Загалом Невикористано (Allocatable - Requested): CPU={format_cpu(unused_total_cpu)}, Пам'ять={format_memory(unused_total_memory)}")
        if cluster_totals["usage_cpu"] > 0 or cluster_totals["usage_memory"] > 0:
            usage_cpu_total_p = (cluster_totals["usage_cpu"] / cluster_totals["allocatable_cpu"] * 100) if cluster_totals["allocatable_cpu"] > 0 else 0
            usage_mem_total_p = (cluster_totals["usage_memory"] / cluster_totals["allocatable_memory"] * 100) if cluster_totals["allocatable_memory"] > 0 else 0
            print(f"- Загальне Реальне Використання (з top node): CPU={format_cpu(cluster_totals['usage_cpu'])} ({usage_cpu_total_p:.1f}%), Пам'ять={format_memory(cluster_totals['usage_memory'])} ({usage_mem_total_p:.1f}%)")
            idle_cpu_vs_usage = max(0, cluster_totals["allocatable_cpu"] - cluster_totals["usage_cpu"])
            idle_mem_vs_usage = max(0, cluster_totals["allocatable_memory"] - cluster_totals["usage_memory"])
            print(f"- Загалом Вільні (Allocatable - Usage): CPU={format_cpu(idle_cpu_vs_usage)}, Пам'ять={format_memory(idle_mem_vs_usage)}")
            if cluster_totals["requested_cpu"] > 0 and cluster_totals["usage_cpu"] < cluster_totals["requested_cpu"] * USAGE_VS_REQUEST_LOW_RATIO :
                 print(f"  ПОПЕРЕДЖЕННЯ: Загальне використання CPU ({format_cpu(cluster_totals['usage_cpu'])}) значно нижче загальних запитів CPU ({format_cpu(cluster_totals['requested_cpu'])}). Можливо, запити завищені.")
            if cluster_totals["requested_memory"] > 0 and cluster_totals["usage_memory"] < cluster_totals["requested_memory"] * USAGE_VS_REQUEST_LOW_RATIO:
                 print(f"  ПОПЕРЕДЖЕННЯ: Загальне використання Memory ({format_memory(cluster_totals['usage_memory'])}) значно нижче загальних запитів Memory ({format_memory(cluster_totals['requested_memory'])}). Можливо, запити завищені.")

        print("\nЗагальні Рекомендації та Спостереження по Кластеру:")
        # --- Рекомендації щодо типів EC2 інстансів (тільки для фінального звіту) ---
        if step_name == "Фінальний звіт":
            ec2_instances = parse_ec2_instance_data(EC2_INSTANCE_DATA_FILE)
            if ec2_instances and cluster_totals["requested_cpu"] > 0 and cluster_totals["requested_memory"] > 0:
                print("\n  --- Рекомендації щодо вибору ОДИНОЧНОГО типу EC2 інстансу ---")
                best_single_instance_option = None
                min_single_instance_cost = float('inf')
                
                buffered_requested_cpu = cluster_totals["requested_cpu"] * 1.15
                buffered_requested_memory = cluster_totals["requested_memory"] * 1.15
                print(f"    Загальні запити кластера (з буфером 15%): CPU={format_cpu(buffered_requested_cpu)}, Пам'ять={format_memory(buffered_requested_memory)}")

                suitable_instances = []
                for instance in ec2_instances:
                    if instance["cpu_millicores"] >= buffered_requested_cpu and \
                       instance["memory_mib"] >= buffered_requested_memory:
                        suitable_instances.append(instance)
                
                if suitable_instances:
                    # Сортуємо підходящі інстанси за ціною
                    suitable_instances.sort(key=lambda x: x["price_per_hour"])
                    best_single_instance_option = suitable_instances[0] # Беремо найдешевший з підходящих

                    print(f"    Найбільш економічно вигідний ОДИНОЧНИЙ інстанс, що вміщує все навантаження (запити + 15% буфер):")
                    print(f"      - Тип інстансу: {best_single_instance_option['name']}")
                    print(f"        - CPU інстансу: {format_cpu(best_single_instance_option['cpu_millicores'])}")
                    print(f"        - Пам'ять інстансу: {format_memory(best_single_instance_option['memory_mib'])}")
                    print(f"        - Орієнтовна вартість: ${best_single_instance_option['price_per_hour']:.4f} / год")
                else:
                    print("    Не вдалося знайти жодного ОДИНОЧНОГО типу EC2 інстансу, який би вмістив все поточне навантаження.")
            
            elif not ec2_instances and step_name == "Фінальний звіт": # Додано перевірку на step_name
                 print("    ПОПЕРЕДЖЕННЯ: Дані про EC2 інстанси не завантажені або не містять валідних записів. Рекомендації щодо вартості неможливі.")


        critical_nodes_mem_limit_overcommit = sorted(list(set(n for n, d in all_node_analyses.items() if d and any("ЗАБОРОНЕНО: Memory Limits Overcommit" in err for err in d.get("errors",[])))) ) 
        critical_nodes_cpu_limit_overcommit = sorted(list(set(n for n, d in all_node_analyses.items() if d and any("КРИТИЧНО: CPU Limits Overcommit" in err for err in d.get("errors",[]))))) 
        if critical_nodes_mem_limit_overcommit: print(f"  - КРИТИЧНО (Memory Limit Overcommit > {MEM_LIMIT_OVERCOMMIT_CRITICAL_THRESHOLD}%): Вузли: {', '.join(critical_nodes_mem_limit_overcommit)}. НЕГАЙНО ВИПРАВТЕ!")
        if critical_nodes_cpu_limit_overcommit: print(f"  - КРИТИЧНО (CPU Limit Overcommit > {CPU_LIMIT_OVERCOMMIT_CRITICAL_THRESHOLD}%): Вузли: {', '.join(critical_nodes_cpu_limit_overcommit)}. НЕГАЙНО ВИПРАВТЕ!")
        warn_nodes_high_resource_pods = sorted(list(set(n for n, d in all_node_analyses.items() if d and any("Поди з високими запитами" in warn for warn in d.get("warnings", [])))) ) 
        if warn_nodes_high_resource_pods: print(f"  - УВАГА (Поди з високими запитами >{POD_HIGH_RESOURCE_REQUEST_THRESHOLD}%): Вузли: {', '.join(warn_nodes_high_resource_pods)}. Перегляньте конфігурацію цих подів.")
        warn_nodes_no_requests = sorted(list(set(n for n, d in all_node_analyses.items() if d and any("Поди БЕЗ ЗАПИТІВ" in warn for warn in d.get("warnings", [])))) ) 
        if warn_nodes_no_requests: print(f"  - УВАГА (Поди без запитів): Вузли: {', '.join(warn_nodes_no_requests)}. Встановіть запити для стабільності.")
        warn_nodes_no_limits = sorted(list(set(n for n, d in all_node_analyses.items() if d and any("Поди БЕЗ ЛІМІТІВ" in warn for warn in d.get("warnings", [])))) ) 
        if warn_nodes_no_limits: print(f"  - УВАГА (Поди без лімітів): Вузли: {', '.join(warn_nodes_no_limits)}. Встановіть ліміти для запобігання неконтрольованому споживанню.")
        low_util_nodes_filtered = []
        if all_node_analyses: 
            for n, d_analysis in all_node_analyses.items():
                if not d_analysis: continue 
                node_info_check = cluster_data["nodes"].get(n)
                if not node_info_check or not node_info_check.get("allocatable") or not node_info_check["allocatable"].get("cpu"): continue 
                alloc_cpu_node = node_info_check.get("allocatable",{}).get("cpu",0.0) 
                alloc_mem_node = node_info_check.get("allocatable",{}).get("memory",0.0)
                is_low_util = False
                usage_data = node_info_check.get("usage",{})
                usage_cpu_node = usage_data.get("cpu")
                usage_mem_node = usage_data.get("memory")
                if usage_cpu_node is not None and usage_mem_node is not None and alloc_cpu_node > 0 and alloc_mem_node > 0:
                    if (usage_cpu_node / alloc_cpu_node * 100 < NODE_LOW_UTILIZATION_THRESHOLD) and \
                       (usage_mem_node / alloc_mem_node * 100 < NODE_LOW_UTILIZATION_THRESHOLD): is_low_util = True
                elif alloc_cpu_node > 0 and alloc_mem_node > 0 and \
                     (d_analysis.get("req_cpu_p", 101.0) < NODE_LOW_UTILIZATION_THRESHOLD and \
                      d_analysis.get("req_mem_p", 101.0) < NODE_LOW_UTILIZATION_THRESHOLD): is_low_util = True
                if is_low_util: low_util_nodes_filtered.append(n)
        high_util_nodes_filtered = []
        if all_node_analyses:
            for n, d_analysis in all_node_analyses.items():
                if not d_analysis: continue 
                node_info_check = cluster_data["nodes"].get(n)
                if not node_info_check or not node_info_check.get("allocatable") or not node_info_check["allocatable"].get("cpu"): continue
                alloc_cpu_node = node_info_check.get("allocatable",{}).get("cpu",0.0)
                alloc_mem_node = node_info_check.get("allocatable",{}).get("memory",0.0)
                is_high_util = False
                usage_data = node_info_check.get("usage",{})
                usage_cpu_node = usage_data.get("cpu")
                usage_mem_node = usage_data.get("memory")
                if usage_cpu_node is not None and usage_mem_node is not None and alloc_cpu_node > 0 and alloc_mem_node > 0 :
                     if (usage_cpu_node / alloc_cpu_node * 100 > NODE_HIGH_UTILIZATION_THRESHOLD) or \
                        (usage_mem_node / alloc_mem_node * 100 > NODE_HIGH_UTILIZATION_THRESHOLD): is_high_util = True
                elif alloc_cpu_node > 0 and alloc_mem_node > 0 and \
                     (d_analysis.get("req_cpu_p", 0.0) > NODE_HIGH_UTILIZATION_THRESHOLD or \
                      d_analysis.get("req_mem_p", 0.0) > NODE_HIGH_UTILIZATION_THRESHOLD): is_high_util = True
                if is_high_util: high_util_nodes_filtered.append(n)
        recommendations_found_count = sum(len(d.get("recommendations",[])) + len(d.get("warnings",[])) + len(d.get("errors",[])) + len(d.get("flags",[])) for d in all_node_analyses.values() if d) 
        if len(low_util_nodes_filtered) > 0 and len([n for n,d in all_node_analyses.items() if d]) > 1 : 
            print(f"  - ОПТИМІЗАЦІЯ ВАРТОСТІ/РОЗПОДІЛУ: Є вузли з потенційно низьким завантаженням (< {NODE_LOW_UTILIZATION_THRESHOLD}% usage/requests): {', '.join(sorted(list(set(low_util_nodes_filtered))))}.")
            if len(high_util_nodes_filtered) > 0:
                 print(f"    При цьому є вузли з високим завантаженням (> {NODE_HIGH_UTILIZATION_THRESHOLD}% usage/requests): {', '.join(sorted(list(set(high_util_nodes_filtered))))}.")
                 print(f"    РЕКОМЕНДАЦІЯ: Розгляньте перерозподіл подів для більш рівномірного завантаження та можливої консолідації.")
            else:
                 print(f"    РЕКОМЕНДАЦІЯ: Розгляньте консолідацію навантаження для зменшення кількості вузлів, якщо це можливо.")
            recommendations_found_count +=1
        if recommendations_found_count == 0: print("  - Загальний стан ресурсів кластера виглядає задовільним на основі наданих даних.")
        print("  - Примітка: Оптимальне використання вузлів передбачає їх достатнє завантаження без надмірного overcommit'у ресурсів, особливо пам'яті.")
    else: print("Недостатньо даних про доступні ресурси (allocatable) для повного загального аналізу кластера.")

def main():
    file_paths = [
        "01_namespace_sample.txt",          
        "02_describe_node.txt",             
        "03_describe_node_another.txt",     
        "04_describe_pod.txt",              
        "05_describe_pod_another.txt",
        "06_top_node.txt", 
        "07_top_pod_xenia-stg.txt" 
    ]
    command_types: Dict[str, Tuple[str, Optional[str]]] = {
        "01_namespace_sample.txt": ("get_pods", TARGET_NAMESPACE),
        "02_describe_node.txt": ("describe_node", None),
        "03_describe_node_another.txt": ("describe_node", None),
        "04_describe_pod.txt": ("describe_pod", None), 
        "05_describe_pod_another.txt": ("describe_pod", None),
        "06_top_node.txt": ("top_node", None),
        "07_top_pod_xenia-stg.txt": ("top_pod", TARGET_NAMESPACE)
    }
    
    def sort_key(fp_tuple): 
        cmd_type, _ = fp_tuple[1]
        if "get_pods" == cmd_type: return 0
        if "describe_node" == cmd_type: return 1
        if "describe_pod" == cmd_type: return 2
        if "top_node" == cmd_type: return 3
        if "top_pod" == cmd_type: return 4
        return 5

    existing_files_with_types = []
    for fp in file_paths:
        if os.path.exists(fp):
            basename = os.path.basename(fp)
            cmd_tuple = command_types.get(basename)
            if cmd_tuple: existing_files_with_types.append((fp, cmd_tuple))
            else: print(f"\nПопередження: Не знайдено тип команди для файлу {basename}, пропускається.")
    if not existing_files_with_types: print("Не знайдено жодного файлу для обробки."); return
    existing_files_with_types.sort(key=sort_key)

    for file_path, (command_type, namespace_param) in existing_files_with_types:
        print(f"\n=== Обробка файлу: {file_path} ===")
        try:
            with open(file_path, 'r', encoding='utf-8') as f: content = f.read().strip() 
            if not content: print(f"Попередження: Файл {file_path} порожній."); continue
        except Exception as e: print(f"Помилка читання файлу {file_path}: {e}"); continue
        
        try:
            if command_type == "get_pods": parse_get_pods(content, namespace_param)
            elif command_type == "describe_node": parse_describe_node(content)
            elif command_type == "describe_pod": parse_describe_pod(content)
            elif command_type == "top_node": parse_top_nodes(content)
            elif command_type == "top_pod": parse_top_pods(content, namespace_param)
            
            if command_type in ["get_pods", "describe_node", "describe_pod", "top_node", "top_pod"]:
                 analyze_resources(f"Після {command_type} ({os.path.basename(file_path)})")
        except Exception as e:
            print(f"\n!!! Помилка під час обробки файлу {file_path} (Команда: {command_type}): {e} !!!")
            import traceback
            traceback.print_exc()

    print("\n=== Завершення аналізу всіх файлів ===")
    analyze_resources("Фінальний звіт")

if __name__ == "__main__":
    main()
