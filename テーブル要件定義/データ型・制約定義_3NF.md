# データ型・制約定義（3NF対応）

版数: v1.0  
最終更新日: 2026-03-21

## 1. 前提
- 対象: Django モデル `django/scraper/models.py` ベース
- DB 想定: PostgreSQL
- 主キー: `DEFAULT_AUTO_FIELD = django.db.models.BigAutoField` のため、各テーブルの `id` は `bigint` 自動採番

## 2. 共通ルール
- `CharField` / `URLField` は `null=False`（DB上は NOT NULL）。`blank=True` は空文字許容であり、NULL許容ではない。
- `ForeignKey(..., null=True)` は NULL 許容。
- `OneToOneField` は DB 上 `UNIQUE` 制約を持つ。
- `auto_now_add=True` は INSERT 時刻自動設定、`auto_now=True` は UPDATE 時刻自動更新。
- `PositiveIntegerField` は Django バリデーションで 0 以上を想定（DB の CHECK 制約は明示追加していない）。

## 3. テーブル定義

### 3.1 マスタ系（3NF参照テーブル）

#### Manufacturer
| カラム | 型 | NULL | 主要制約 |
|---|---|---|---|
| id | bigint | NO | PK |
| name | varchar(100) | NO | UNIQUE |

#### SocketType
| カラム | 型 | NULL | 主要制約 |
|---|---|---|---|
| id | bigint | NO | PK |
| name | varchar(50) | NO | UNIQUE |

#### MemoryType
| カラム | 型 | NULL | 主要制約 |
|---|---|---|---|
| id | bigint | NO | PK |
| name | varchar(20) | NO | UNIQUE |

#### FormFactor
| カラム | 型 | NULL | 主要制約 |
|---|---|---|---|
| id | bigint | NO | PK |
| name | varchar(30) | NO | UNIQUE |

#### InterfaceType
| カラム | 型 | NULL | 主要制約 |
|---|---|---|---|
| id | bigint | NO | PK |
| name | varchar(30) | NO | UNIQUE |

#### EfficiencyGrade
| カラム | 型 | NULL | 主要制約 |
|---|---|---|---|
| id | bigint | NO | PK |
| name | varchar(20) | NO | UNIQUE |

#### OSFamily
| カラム | 型 | NULL | 主要制約 |
|---|---|---|---|
| id | bigint | NO | PK |
| name | varchar(30) | NO | UNIQUE |

#### OSEdition
| カラム | 型 | NULL | 主要制約 |
|---|---|---|---|
| id | bigint | NO | PK |
| name | varchar(50) | NO | UNIQUE |

#### LicenseType
| カラム | 型 | NULL | 主要制約 |
|---|---|---|---|
| id | bigint | NO | PK |
| name | varchar(30) | NO | UNIQUE |

### 3.2 PCPart（部品マスタ）

| カラム | 型 | NULL | 主要制約 |
|---|---|---|---|
| id | bigint | NO | PK |
| part_type | varchar(20) | NO | choices 制約（cpu, cpu_cooler, gpu, motherboard, memory, storage, os, psu, case） |
| name | varchar(200) | NO | `part_type + name` 複合 UNIQUE |
| price | integer | NO |  |
| specs | jsonb | NO | default `{}` |
| url | varchar(200) | NO | URLField |
| maker | varchar(100) | NO | INDEX, 空文字可 |
| manufacturer_id | bigint | YES | FK -> Manufacturer(id), ON DELETE SET NULL |
| model_code | varchar(120) | NO | INDEX, 空文字可 |
| shop_code | varchar(50) | YES | INDEX |
| socket | varchar(50) | NO | INDEX, 空文字可 |
| memory_type | varchar(20) | NO | INDEX, 空文字可 |
| chipset | varchar(50) | NO | INDEX, 空文字可 |
| form_factor | varchar(30) | NO | INDEX, 空文字可 |
| cores | integer | YES | PositiveIntegerField |
| threads | integer | YES | PositiveIntegerField |
| tdp_w | integer | YES | PositiveIntegerField |
| base_clock_mhz | integer | YES | PositiveIntegerField |
| boost_clock_mhz | integer | YES | PositiveIntegerField |
| vram_gb | integer | YES | PositiveIntegerField |
| vram_type | varchar(20) | NO | 空文字可 |
| wattage | integer | YES | PositiveIntegerField |
| efficiency_grade | varchar(20) | NO | 空文字可 |
| capacity_gb | integer | YES | PositiveIntegerField, INDEX |
| speed_mhz | integer | YES | PositiveIntegerField |
| interface | varchar(30) | NO | INDEX, 空文字可 |
| m2_slots | integer | YES | PositiveIntegerField |
| pcie_x16_slots | integer | YES | PositiveIntegerField |
| usb_total | integer | YES | PositiveIntegerField |
| type_c_ports | integer | YES | PositiveIntegerField |
| included_fan_count | integer | YES | PositiveIntegerField |
| supported_fan_count | integer | YES | PositiveIntegerField |
| max_tdp_w | integer | YES | PositiveIntegerField |
| os_family | varchar(30) | NO | 空文字可 |
| os_edition | varchar(50) | NO | 空文字可 |
| license_type | varchar(30) | NO | 空文字可 |
| currency | varchar(3) | NO | default `JPY` |
| stock_status | varchar(20) | NO | default `unknown` |
| is_active | boolean | NO | default true, INDEX |
| last_scraped_at | timestamp with time zone | YES |  |
| scraped_at | timestamp with time zone | NO | auto_now_add |
| updated_at | timestamp with time zone | NO | auto_now |

補足制約:
- 複合一意制約: `(part_type, name)`
- 並び順デフォルト: `updated_at DESC`

### 3.3 詳細テーブル（2NF分離）

#### CPUDetail
| カラム | 型 | NULL | 主要制約 |
|---|---|---|---|
| id | bigint | NO | PK |
| part_id | bigint | NO | UNIQUE, FK -> PCPart(id), ON DELETE CASCADE |
| socket | varchar(50) | NO | INDEX, 空文字可 |
| memory_type | varchar(20) | NO | INDEX, 空文字可 |
| socket_ref_id | bigint | YES | FK -> SocketType(id), ON DELETE SET NULL |
| memory_type_ref_id | bigint | YES | FK -> MemoryType(id), ON DELETE SET NULL |
| cores | integer | YES | PositiveIntegerField |
| threads | integer | YES | PositiveIntegerField |
| tdp_w | integer | YES | PositiveIntegerField |
| base_clock_mhz | integer | YES | PositiveIntegerField |
| boost_clock_mhz | integer | YES | PositiveIntegerField |

#### GPUDetail
| カラム | 型 | NULL | 主要制約 |
|---|---|---|---|
| id | bigint | NO | PK |
| part_id | bigint | NO | UNIQUE, FK -> PCPart(id), ON DELETE CASCADE |
| vram_gb | integer | YES | PositiveIntegerField |
| vram_type | varchar(20) | NO | 空文字可 |
| tdp_w | integer | YES | PositiveIntegerField |
| interface | varchar(30) | NO | INDEX, 空文字可 |
| interface_ref_id | bigint | YES | FK -> InterfaceType(id), ON DELETE SET NULL |

#### MotherboardDetail
| カラム | 型 | NULL | 主要制約 |
|---|---|---|---|
| id | bigint | NO | PK |
| part_id | bigint | NO | UNIQUE, FK -> PCPart(id), ON DELETE CASCADE |
| socket | varchar(50) | NO | INDEX, 空文字可 |
| memory_type | varchar(20) | NO | INDEX, 空文字可 |
| socket_ref_id | bigint | YES | FK -> SocketType(id), ON DELETE SET NULL |
| memory_type_ref_id | bigint | YES | FK -> MemoryType(id), ON DELETE SET NULL |
| chipset | varchar(50) | NO | INDEX, 空文字可 |
| form_factor | varchar(30) | NO | INDEX, 空文字可 |
| form_factor_ref_id | bigint | YES | FK -> FormFactor(id), ON DELETE SET NULL |
| m2_slots | integer | YES | PositiveIntegerField |
| pcie_x16_slots | integer | YES | PositiveIntegerField |
| usb_total | integer | YES | PositiveIntegerField |
| type_c_ports | integer | YES | PositiveIntegerField |

#### MemoryDetail
| カラム | 型 | NULL | 主要制約 |
|---|---|---|---|
| id | bigint | NO | PK |
| part_id | bigint | NO | UNIQUE, FK -> PCPart(id), ON DELETE CASCADE |
| memory_type | varchar(20) | NO | INDEX, 空文字可 |
| memory_type_ref_id | bigint | YES | FK -> MemoryType(id), ON DELETE SET NULL |
| capacity_gb | integer | YES | PositiveIntegerField, INDEX |
| speed_mhz | integer | YES | PositiveIntegerField |
| form_factor | varchar(30) | NO | INDEX, 空文字可 |
| form_factor_ref_id | bigint | YES | FK -> FormFactor(id), ON DELETE SET NULL |

#### StorageDetail
| カラム | 型 | NULL | 主要制約 |
|---|---|---|---|
| id | bigint | NO | PK |
| part_id | bigint | NO | UNIQUE, FK -> PCPart(id), ON DELETE CASCADE |
| capacity_gb | integer | YES | PositiveIntegerField, INDEX |
| interface | varchar(30) | NO | INDEX, 空文字可 |
| form_factor | varchar(30) | NO | INDEX, 空文字可 |
| interface_ref_id | bigint | YES | FK -> InterfaceType(id), ON DELETE SET NULL |
| form_factor_ref_id | bigint | YES | FK -> FormFactor(id), ON DELETE SET NULL |

#### OSDetail
| カラム | 型 | NULL | 主要制約 |
|---|---|---|---|
| id | bigint | NO | PK |
| part_id | bigint | NO | UNIQUE, FK -> PCPart(id), ON DELETE CASCADE |
| os_family | varchar(30) | NO | 空文字可 |
| os_edition | varchar(50) | NO | 空文字可 |
| license_type | varchar(30) | NO | 空文字可 |
| os_family_ref_id | bigint | YES | FK -> OSFamily(id), ON DELETE SET NULL |
| os_edition_ref_id | bigint | YES | FK -> OSEdition(id), ON DELETE SET NULL |
| license_type_ref_id | bigint | YES | FK -> LicenseType(id), ON DELETE SET NULL |

#### PSUDetail
| カラム | 型 | NULL | 主要制約 |
|---|---|---|---|
| id | bigint | NO | PK |
| part_id | bigint | NO | UNIQUE, FK -> PCPart(id), ON DELETE CASCADE |
| wattage | integer | YES | PositiveIntegerField |
| efficiency_grade | varchar(20) | NO | 空文字可 |
| form_factor | varchar(30) | NO | INDEX, 空文字可 |
| efficiency_grade_ref_id | bigint | YES | FK -> EfficiencyGrade(id), ON DELETE SET NULL |
| form_factor_ref_id | bigint | YES | FK -> FormFactor(id), ON DELETE SET NULL |

#### CaseDetail
| カラム | 型 | NULL | 主要制約 |
|---|---|---|---|
| id | bigint | NO | PK |
| part_id | bigint | NO | UNIQUE, FK -> PCPart(id), ON DELETE CASCADE |
| form_factor | varchar(30) | NO | INDEX, 空文字可 |
| form_factor_ref_id | bigint | YES | FK -> FormFactor(id), ON DELETE SET NULL |
| included_fan_count | integer | YES | PositiveIntegerField |
| supported_fan_count | integer | YES | PositiveIntegerField |

#### CPUCoolerDetail
| カラム | 型 | NULL | 主要制約 |
|---|---|---|---|
| id | bigint | NO | PK |
| part_id | bigint | NO | UNIQUE, FK -> PCPart(id), ON DELETE CASCADE |
| socket | varchar(50) | NO | INDEX, 空文字可 |
| socket_ref_id | bigint | YES | FK -> SocketType(id), ON DELETE SET NULL |
| max_tdp_w | integer | YES | PositiveIntegerField |
| form_factor | varchar(30) | NO | INDEX, 空文字可 |
| form_factor_ref_id | bigint | YES | FK -> FormFactor(id), ON DELETE SET NULL |

### 3.4 Configuration（構成結果）

| カラム | 型 | NULL | 主要制約 |
|---|---|---|---|
| id | bigint | NO | PK |
| budget | integer | NO |  |
| usage | varchar(20) | NO | choices 制約（gaming, video_editing, general） |
| cpu_id | bigint | YES | FK -> PCPart(id), ON DELETE SET NULL |
| cpu_cooler_id | bigint | YES | FK -> PCPart(id), ON DELETE SET NULL |
| gpu_id | bigint | YES | FK -> PCPart(id), ON DELETE SET NULL |
| motherboard_id | bigint | YES | FK -> PCPart(id), ON DELETE SET NULL |
| memory_id | bigint | YES | FK -> PCPart(id), ON DELETE SET NULL |
| storage_id | bigint | YES | FK -> PCPart(id), ON DELETE SET NULL |
| storage2_id | bigint | YES | FK -> PCPart(id), ON DELETE SET NULL |
| storage3_id | bigint | YES | FK -> PCPart(id), ON DELETE SET NULL |
| os_id | bigint | YES | FK -> PCPart(id), ON DELETE SET NULL |
| psu_id | bigint | YES | FK -> PCPart(id), ON DELETE SET NULL |
| case_id | bigint | YES | FK -> PCPart(id), ON DELETE SET NULL |
| total_price | integer | NO | default 0 |
| created_at | timestamp with time zone | NO | auto_now_add |
| is_deleted | boolean | NO | default false, INDEX |
| deleted_at | timestamp with time zone | YES |  |

補足制約:
- 並び順デフォルト: `created_at DESC`

### 3.5 ScraperStatus（運用状態）

| カラム | 型 | NULL | 主要制約 |
|---|---|---|---|
| id | bigint | NO | PK |
| last_run | timestamp with time zone | YES |  |
| next_run | timestamp with time zone | YES |  |
| total_scraped | integer | NO | default 0 |
| success_count | integer | NO | default 0 |
| error_count | integer | NO | default 0 |
| cache_enabled | boolean | NO | default true |
| cache_ttl_seconds | integer | NO | default 3600 |
| updated_at | timestamp with time zone | NO | auto_now |

## 4. 追加推奨（将来）
- DB レベル CHECK 制約を追加: `price >= 0`, `budget >= 0`, `total_price >= 0` など。
- 業務キーの一意性強化: `shop_code` の一意制約導入可否を検討。
- NULL と空文字が混在する列（例: 文字列属性）の運用ルールを統一。
