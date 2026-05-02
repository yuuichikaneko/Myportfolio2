# ER図（第3正規化対応・簡略版）

```mermaid
erDiagram
    PCPart {
        bigint id PK
        varchar part_type
        varchar name
        int price
        varchar maker
        bool is_active
        datetime updated_at
    }

    CPUDetail {
        bigint id PK
        bigint part_id FK UK
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
        int tdp_w
    }

    MotherboardDetail {
        bigint id PK
        bigint part_id FK UK
        varchar chipset
        int m2_slots
        int pcie_x16_slots
    }

    MemoryDetail {
        bigint id PK
        bigint part_id FK UK
        int capacity_gb
        int speed_mhz
    }

    StorageDetail {
        bigint id PK
        bigint part_id FK UK
        int capacity_gb
    }

    PSUDetail {
        bigint id PK
        bigint part_id FK UK
        int wattage
    }

    CaseDetail {
        bigint id PK
        bigint part_id FK UK
        int included_fan_count
        int supported_fan_count
    }

    CPUCoolerDetail {
        bigint id PK
        bigint part_id FK UK
        int max_tdp_w
    }

    OSDetail {
        bigint id PK
        bigint part_id FK UK
        varchar os_family
        varchar os_edition
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
    }

    PCPart ||--o| CPUDetail : has
    PCPart ||--o| GPUDetail : has
    PCPart ||--o| MotherboardDetail : has
    PCPart ||--o| MemoryDetail : has
    PCPart ||--o| StorageDetail : has
    PCPart ||--o| PSUDetail : has
    PCPart ||--o| CaseDetail : has
    PCPart ||--o| CPUCoolerDetail : has
    PCPart ||--o| OSDetail : has

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
