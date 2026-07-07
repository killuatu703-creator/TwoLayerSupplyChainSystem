from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree as ET

from .models import OrderInformation

SHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PACKAGE_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


@dataclass(slots=True)
class SupplierConfig:
    # Excel 中读取出来的 supplier 初期设定。

    supplier_name: str
    role: str
    mark_up: float
    transportation_cost: int
    specialty_job_remainder: int | None
    specialty_discount: int
    ga_seed: int
    flow_time_weight: float


@dataclass(slots=True)
class ExperimentConfig:
    # 一次实验需要的全部初期数据。

    disaster_start_time: int
    disaster_end_time: int
    resource_count: int
    rules: list[str]
    ga_population_size: int
    ga_generations: int
    ga_mutation_rate: float
    ga_crossover_rate: float
    s1_orders: list[OrderInformation]
    supplier_initial_orders: dict[str, list[OrderInformation]]
    suppliers: dict[str, SupplierConfig]

    def supplier(self, supplier_name: str) -> SupplierConfig:
        # 按名字取得 supplier 设定。
        try:
            return self.suppliers[supplier_name]
        except KeyError as exc:
            raise KeyError(f"Excel suppliers sheet 缺少 {supplier_name} 的设定") from exc


class SimpleXlsxWorkbook:
    # 只读取本项目需要的简单 xlsx 表格，不依赖 openpyxl。

    def __init__(self, path: Path):
        self.path = path
        self._zip = zipfile.ZipFile(path)
        self._shared_strings = self._read_shared_strings()
        self._sheet_paths = self._read_sheet_paths()

    def sheet_rows(self, sheet_name: str) -> list[list[str]]:
        # 返回指定 sheet 的二维表数据。
        if sheet_name not in self._sheet_paths:
            raise KeyError(f"Excel 文件中找不到 sheet: {sheet_name}")
        xml_path = self._sheet_paths[sheet_name]
        root = ET.fromstring(self._zip.read(xml_path))
        rows: list[list[str]] = []
        for row in root.findall(f".//{SHEET_NS}sheetData/{SHEET_NS}row"):
            values: dict[int, str] = {}
            for cell in row.findall(f"{SHEET_NS}c"):
                cell_ref = cell.attrib.get("r", "A1")
                col = _column_index(cell_ref)
                values[col] = self._cell_value(cell)
            if values:
                max_col = max(values)
                rows.append([values.get(i, "") for i in range(max_col + 1)])
            else:
                rows.append([])
        return rows

    def _read_shared_strings(self) -> list[str]:
        if "xl/sharedStrings.xml" not in self._zip.namelist():
            return []
        root = ET.fromstring(self._zip.read("xl/sharedStrings.xml"))
        strings: list[str] = []
        for si in root.findall(f"{SHEET_NS}si"):
            texts = [node.text or "" for node in si.findall(f".//{SHEET_NS}t")]
            strings.append("".join(texts))
        return strings

    def _read_sheet_paths(self) -> dict[str, str]:
        workbook = ET.fromstring(self._zip.read("xl/workbook.xml"))
        rels = ET.fromstring(self._zip.read("xl/_rels/workbook.xml.rels"))
        rel_map = {
            rel.attrib["Id"]: rel.attrib["Target"]
            for rel in rels.findall(f"{PACKAGE_REL_NS}Relationship")
        }
        sheet_paths: dict[str, str] = {}
        for sheet in workbook.findall(f".//{SHEET_NS}sheets/{SHEET_NS}sheet"):
            name = sheet.attrib["name"]
            rel_id = sheet.attrib[f"{REL_NS}id"]
            target = rel_map[rel_id]
            if target.startswith("/"):
                path = target.lstrip("/")
            else:
                path = f"xl/{target}"
            sheet_paths[name] = path
        return sheet_paths

    def _cell_value(self, cell: ET.Element) -> str:
        cell_type = cell.attrib.get("t")
        if cell_type == "inlineStr":
            texts = [node.text or "" for node in cell.findall(f".//{SHEET_NS}t")]
            return "".join(texts).strip()
        value = cell.find(f"{SHEET_NS}v")
        if value is None or value.text is None:
            return ""
        raw = value.text.strip()
        if cell_type == "s":
            return self._shared_strings[int(raw)]
        return raw


def load_experiment_from_excel(path: Path) -> ExperimentConfig:
    # 从 input_data.xlsx 读取实验设定和所有初期 Job。
    if not path.exists():
        raise FileNotFoundError(
            f"找不到 Excel 输入文件: {path}\n"
            "请确认项目根目录下存在 input_data.xlsx。"
        )

    workbook = SimpleXlsxWorkbook(path)
    settings = _read_key_value_sheet(workbook.sheet_rows("settings"))
    resource_count = _as_int(settings.get("resource_count"), "resource_count")
    fallback_resource_names = [f"R{i}" for i in range(1, resource_count + 1)]

    suppliers = _read_suppliers(workbook.sheet_rows("suppliers"))
    s1_orders = _read_orders(workbook.sheet_rows("s1_orders"), fallback_resource_names)
    supplier_orders = _read_supplier_orders(
        workbook.sheet_rows("supplier_initial_orders"), fallback_resource_names
    )

    rules = [rule.strip().upper() for rule in settings.get("rules", "GREEDY,TABU,SA").split(",")]
    rules = [rule for rule in rules if rule]

    return ExperimentConfig(
        disaster_start_time=_as_int(settings.get("disaster_start_time"), "disaster_start_time"),
        disaster_end_time=_as_int(settings.get("disaster_end_time"), "disaster_end_time"),
        resource_count=resource_count,
        rules=rules,
        ga_population_size=_as_int(settings.get("ga_population_size"), "ga_population_size"),
        ga_generations=_as_int(settings.get("ga_generations"), "ga_generations"),
        ga_mutation_rate=_as_float(settings.get("ga_mutation_rate"), "ga_mutation_rate"),
        ga_crossover_rate=_as_float(settings.get("ga_crossover_rate"), "ga_crossover_rate"),
        s1_orders=s1_orders,
        supplier_initial_orders=supplier_orders,
        suppliers=suppliers,
    )


def _read_key_value_sheet(rows: list[list[str]]) -> dict[str, str]:
    data: dict[str, str] = {}
    for row in rows[1:]:
        if len(row) < 2 or not row[0]:
            continue
        data[str(row[0]).strip()] = str(row[1]).strip()
    return data


def _read_suppliers(rows: list[list[str]]) -> dict[str, SupplierConfig]:
    records = _rows_to_dicts(rows)
    suppliers: dict[str, SupplierConfig] = {}
    for record in records:
        name = record["supplier_name"]
        suppliers[name] = SupplierConfig(
            supplier_name=name,
            role=record.get("role", "outsource"),
            mark_up=_as_float(record.get("mark_up", "0"), f"{name}.mark_up"),
            transportation_cost=_as_int(
                record.get("transportation_cost", "0"), f"{name}.transportation_cost"
            ),
            specialty_job_remainder=_as_optional_int(
                record.get("specialty_job_remainder", "")
            ),
            specialty_discount=_as_int(
                record.get("specialty_discount", "0"), f"{name}.specialty_discount"
            ),
            ga_seed=_as_int(record.get("ga_seed", "1"), f"{name}.ga_seed"),
            flow_time_weight=_as_float(
                record.get("flow_time_weight", "0"), f"{name}.flow_time_weight"
            ),
        )
    return suppliers


def _read_supplier_orders(
    rows: list[list[str]], resource_names: list[str]
) -> dict[str, list[OrderInformation]]:
    supplier_orders: dict[str, list[OrderInformation]] = {}
    for record in _rows_to_dicts(rows):
        supplier_name = record["supplier_name"]
        supplier_orders.setdefault(supplier_name, []).append(
            _record_to_order(record, resource_names)
        )
    return supplier_orders


def _read_orders(rows: list[list[str]], resource_names: list[str]) -> list[OrderInformation]:
    return [_record_to_order(record, resource_names) for record in _rows_to_dicts(rows)]


def _record_to_order(record: dict[str, str], resource_names: list[str]) -> OrderInformation:
    operations = _operation_sequence(record, resource_names)
    return OrderInformation(
        name_of_job=record["job_name"],
        operations=operations,
        duedate_of_job=_as_int(record.get("due_date"), f"{record['job_name']}.due_date"),
        price_of_job=_as_int(record.get("price"), f"{record['job_name']}.price"),
        cost_of_job=_as_int(record.get("cost"), f"{record['job_name']}.cost"),
        delay_penalty_of_job=_as_int(
            record.get("penalty"), f"{record['job_name']}.penalty"
        ),
        name_of_client=record.get("client_name", "ClientA") or "ClientA",
        release_time_of_job=_as_int(
            record.get("release_time", "0"), f"{record['job_name']}.release_time"
        ),
    )


def _operation_sequence(
    record: dict[str, str], resource_names: list[str]
) -> list[tuple[str, int]]:
    # 从 Excel 的 op 列生成每个 Job 的加工顺序。
    #
    # 新格式使用 op1_resource/op1_time、op2_resource/op2_time ... 这样的列，
    # 因此每个 Job 都可以有不同的 Resource 加工顺序，更接近 Job shop 形式。
    # 如果 Excel 中没有 op 列，则保留对旧格式 R1、R2 ... 列的读取，
    # 这样以前的输入文件也还能继续运行。
    #
    operation_indexes = sorted(
        {
            int(match.group(1))
            for key in record
            if (match := re.fullmatch(r"op(\d+)_resource", key))
        }
    )
    if operation_indexes:
        operations: list[tuple[str, int]] = []
        for index in operation_indexes:
            resource = record.get(f"op{index}_resource", "").strip()
            process_time = record.get(f"op{index}_time", "").strip()
            if not resource and not process_time:
                continue
            if not resource:
                raise ValueError(f"Excel 中 {record['job_name']}.op{index}_resource 不能为空")
            operations.append(
                (
                    resource,
                    _as_int(process_time, f"{record['job_name']}.op{index}_time"),
                )
            )
        if operations:
            return operations

    return [
        (
            resource_name,
            _as_int(record.get(resource_name, "0"), f"{record['job_name']}.{resource_name}"),
        )
        for resource_name in resource_names
        if record.get(resource_name, "") != ""
    ]


def _rows_to_dicts(rows: list[list[str]]) -> list[dict[str, str]]:
    if not rows:
        return []
    headers = [str(header).strip() for header in rows[0]]
    records: list[dict[str, str]] = []
    for row in rows[1:]:
        record = {
            header: str(row[index]).strip() if index < len(row) else ""
            for index, header in enumerate(headers)
            if header
        }
        if any(record.values()):
            records.append(record)
    return records


def _column_index(cell_ref: str) -> int:
    letters = re.match(r"[A-Z]+", cell_ref)
    if not letters:
        return 0
    index = 0
    for char in letters.group(0):
        index = index * 26 + ord(char) - ord("A") + 1
    return index - 1


def _as_int(value: str | None, field_name: str) -> int:
    if value is None or str(value).strip() == "":
        raise ValueError(f"Excel 中 {field_name} 不能为空")
    return int(float(str(value)))


def _as_optional_int(value: str | None) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return int(float(str(value)))


def _as_float(value: str | None, field_name: str) -> float:
    if value is None or str(value).strip() == "":
        raise ValueError(f"Excel 中 {field_name} 不能为空")
    return float(str(value))
