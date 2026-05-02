# ER図（第3正規化対応）

```mermaid
erDiagram
    PCPart {
        bigint id PK
        varchar part_type
        varchar name
        int price
        json specs
        varchar url
        varchar maker
        bigint manufacturer_id FK
        varchar model_code
        varchar shop_code
        varchar socket
        varchar memory_type
        varchar chipset
        varchar form_factor
        int cores
        int threads
        int tdp_w
        int base_clock_mhz
        int boost_clock_mhz
        int vram_gb
        varchar vram_type
        int wattage
        varchar efficiency_grade
        int capacity_gb
        int speed_mhz
        varchar interface
        int m2_slots
        int pcie_x16_slots
        int usb_total
        int type_c_ports
        int included_fan_count
        int supported_fan_count
        int max_tdp_w
        varchar os_family
        varchar os_edition
        varchar license_type
        varchar currency
        varchar stock_status
        bool is_active
        datetime last_scraped_at
        datetime scraped_at
        datetime updated_at
    }

    Manufacturer {
        bigint id PK
        varchar name UK
    }

    SocketType {
        bigint id PK
        varchar name UK
    }

    MemoryType {
        bigint id PK
        varchar name UK
    }

    FormFactor {
        bigint id PK
        varchar name UK
    }

    InterfaceType {
        bigint id PK
        varchar name UK
    }

    EfficiencyGrade {
        bigint id PK
        varchar name UK
    }

    OSFamily {
        bigint id PK
        varchar name UK
    }

    OSEdition {
        bigint id PK
        varchar name UK
    }

    LicenseType {
        bigint id PK
        varchar name UK
    }

    CPUDetail {
        bigint id PK
        bigint part_id FK UK
        varchar socket
        varchar memory_type
        bigint socket_ref_id FK
        bigint memory_type_ref_id FK
        int cores
        int threads
        int tdp_w
        int base_clock_mhz
        int boost_clock_mhz
    }

    GPUDetail {
        bigint id PK
        bigint part_id FK UK
        int vram_gb
        varchar vram_type
        int tdp_w
        varchar interface
        bigint interface_ref_id FK
    }

    MotherboardDetail {
        bigint id PK
        bigint part_id FK UK
        varchar socket
        varchar memory_type
        bigint socket_ref_id FK
        bigint memory_type_ref_id FK
        varchar chipset
        varchar form_factor
        bigint form_factor_ref_id FK
        int m2_slots
        int pcie_x16_slots
        int usb_total
        int type_c_ports
    }

    MemoryDetail {
        bigint id PK
        bigint part_id FK UK
        varchar memory_type
        bigint memory_type_ref_id FK
        int capacity_gb
        int speed_mhz
        varchar form_factor
        bigint form_factor_ref_id FK
    }

    StorageDetail {
        bigint id PK
        bigint part_id FK UK
        int capacity_gb
        varchar interface
        varchar form_factor
        bigint interface_ref_id FK
        bigint form_factor_ref_id FK
    }

    OSDetail {
        bigint id PK
        bigint part_id FK UK
        varchar os_family
        varchar os_edition
        varchar license_type
        bigint os_family_ref_id FK
        bigint os_edition_ref_id FK
        bigint license_type_ref_id FK
    }

    PSUDetail {
        bigint id PK
        bigint part_id FK UK
        int wattage
        varchar efficiency_grade
        varchar form_factor
        bigint efficiency_grade_ref_id FK
        bigint form_factor_ref_id FK
    }

    CaseDetail {
        bigint id PK
        bigint part_id FK UK
        varchar form_factor
        bigint form_factor_ref_id FK
        int included_fan_count
        int supported_fan_count
    }

    CPUCoolerDetail {
        bigint id PK
        bigint part_id FK UK
        varchar socket
        bigint socket_ref_id FK
        int max_tdp_w
        varchar form_factor
        bigint form_factor_ref_id FK
    }

    Configuration {
        bigint id PK
        int budget
        varchar usage
        bigint cpu_id FK
        bigint cpu_cooler_id FK
        bigint gpu_id FK
        bigint motherboard_id FK
        bigint memory_id FK
        bigint storage_id FK
        bigint storage2_id FK
        bigint storage3_id FK
        bigint os_id FK
        bigint psu_id FK
        bigint case_id FK
        int total_price
        datetime created_at
        bool is_deleted
        datetime deleted_at
    }

    ScraperStatus {
        bigint id PK
        datetime last_run
        datetime next_run
        int total_scraped
        int success_count
        int error_count
        bool cache_enabled
        int cache_ttl_seconds
        datetime updated_at
    }

    Manufacturer ||--o{ PCPart : has

    PCPart ||--o| CPUDetail : has
    PCPart ||--o| GPUDetail : has
    PCPart ||--o| MotherboardDetail : has
    PCPart ||--o| MemoryDetail : has
    PCPart ||--o| StorageDetail : has
    PCPart ||--o| OSDetail : has
    PCPart ||--o| PSUDetail : has
    PCPart ||--o| CaseDetail : has
    PCPart ||--o| CPUCoolerDetail : has

    SocketType ||--o{ CPUDetail : socket_ref
    MemoryType ||--o{ CPUDetail : memory_type_ref

    InterfaceType ||--o{ GPUDetail : interface_ref

    SocketType ||--o{ MotherboardDetail : socket_ref
    MemoryType ||--o{ MotherboardDetail : memory_type_ref
    FormFactor ||--o{ MotherboardDetail : form_factor_ref

    MemoryType ||--o{ MemoryDetail : memory_type_ref
    FormFactor ||--o{ MemoryDetail : form_factor_ref

    InterfaceType ||--o{ StorageDetail : interface_ref
    FormFactor ||--o{ StorageDetail : form_factor_ref

    OSFamily ||--o{ OSDetail : os_family_ref
    OSEdition ||--o{ OSDetail : os_edition_ref
    LicenseType ||--o{ OSDetail : license_type_ref

    EfficiencyGrade ||--o{ PSUDetail : efficiency_grade_ref
    FormFactor ||--o{ PSUDetail : form_factor_ref

    FormFactor ||--o{ CaseDetail : form_factor_ref

    SocketType ||--o{ CPUCoolerDetail : socket_ref
    FormFactor ||--o{ CPUCoolerDetail : form_factor_ref

    PCPart ||--o{ Configuration : cpu_id
    PCPart ||--o{ Configuration : cpu_cooler_id
    PCPart ||--o{ Configuration : gpu_id
    PCPart ||--o{ Configuration : motherboard_id
    PCPart ||--o{ Configuration : memory_id
    PCPart ||--o{ Configuration : storage_id
    PCPart ||--o{ Configuration : storage2_id
    PCPart ||--o{ Configuration : storage3_id
    PCPart ||--o{ Configuration : os_id
    PCPart ||--o{ Configuration : psu_id
    PCPart ||--o{ Configuration : case_id
```
