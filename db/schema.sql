-- Wisetech 合約及帳務管理系統 — Supabase (PostgreSQL) schema
-- 在 Supabase Dashboard → SQL Editor 貼上整份執行即可。可重複執行（IF NOT EXISTS）。

-- ───────────────────────── 公司主檔 ─────────────────────────
create table if not exists companies (
    id              bigserial primary key,
    tax_id          varchar(8) unique,                -- 統一編號
    name            text not null,                     -- 正式名稱
    tax_id_status   text not null default 'pending'
                    check (tax_id_status in ('pending','auto','candidate','confirmed','not_found','not_applicable')),
    gcis_name       text,                              -- 商工登記上的名稱
    gcis_status     text,                              -- 商工登記狀態（核准設立／解散…）
    address         text,
    note            text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);
create index if not exists companies_name_idx on companies (name);

-- Excel 上出現過的各種寫法 → 對到同一家公司
create table if not exists company_aliases (
    alias       text primary key,
    company_id  bigint not null references companies(id) on delete cascade,
    match_type  text not null default 'exact'          -- exact / prefix / manual
);
create index if not exists company_aliases_company_idx on company_aliases (company_id);

-- 統編比對候選（商工登記查詢結果，待人工確認）
create table if not exists taxid_candidates (
    id          bigserial primary key,
    company_id  bigint not null references companies(id) on delete cascade,
    tax_id      varchar(8) not null,
    gcis_name   text,
    gcis_status text,
    score       numeric,
    created_at  timestamptz not null default now(),
    unique (company_id, tax_id)
);

create table if not exists contacts (
    id          bigserial primary key,
    company_id  bigint not null references companies(id) on delete cascade,
    name        text,
    phone       text,
    email       text,
    address     text,
    note        text,
    last_seen   date,
    source      text not null default 'manual',
    created_at  timestamptz not null default now()
);
create index if not exists contacts_company_idx on contacts (company_id);

-- ───────────────────────── 人員與帳號 ─────────────────────────
create table if not exists staff (
    name    text primary key,          -- Allen、Andy、Evia、心慧…（Sales／主寫／管考共用）
    active  boolean not null default true
);

create table if not exists app_users (
    id              bigserial primary key,
    username        text not null unique,
    display_name    text not null,
    password_hash   text not null,
    role            text not null check (role in ('admin','staff','sales')),
    staff_name      text references staff(name) on update cascade,  -- sales 只看得到自己的獎金
    active          boolean not null default true,
    created_at      timestamptz not null default now()
);

-- ───────────────────────── 合約 ─────────────────────────
create table if not exists contracts (
    id                  bigserial primary key,
    contract_no         text,                          -- 11503-07（同號偶有重複，故不設 unique）
    company_id          bigint references companies(id) on delete set null,
    company_raw         text,                          -- Excel 原始公司名稱欄
    name_note           text,                          -- 從名稱拆出來的註記：(解約)、(for 凱景)…
    sales               text,                          -- Sales
    partner_sales       text,                          -- 業務（非 Allen & Andy）
    writer              text,                          -- 主寫
    outsource           text,                          -- 委外
    mid_review          text,                          -- 期中管考
    final_review        text,                          -- 期末管考
    start_date          date,
    end_date            date,
    contract_type       text,                          -- 個案／年約／特案／募資
    deposit_deduct      text,                          -- 前金扣／不扣
    tax_mode            text,                          -- 含稅／未稅
    program             text,                          -- 送案別：SBIR、SIIR、CITD…
    status              text,                          -- Pass／Fail／In Process／Closed…
    approved_amount     numeric,                       -- 過案金額
    deposit_total       numeric,                       -- 前金（未稅）合約條件
    mid_rate            text,                          -- 期中抽成條件（15%、NIL…）
    final_rate          text,                          -- 期末抽成條件
    special_terms       text,                          -- 特別約定事項
    ext_commission_rate text,                          -- 外部佣金 %
    ext_commission_paid date,                          -- 外部佣金發放日
    ext_source          text,                          -- 外部佣金來源
    is_terminated       boolean not null default false,
    is_nda              boolean not null default false,
    raw                 jsonb not null default '{}',   -- 原始文字（無法解析的日期／金額）與未對應欄位
    source              text not null default 'manual',-- manual / excel
    source_sheet        text,
    source_row          int,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now()
);
create index if not exists contracts_company_idx on contracts (company_id);
create index if not exists contracts_no_idx on contracts (contract_no);

-- 送案紀錄：同一份合約可以送很多次（Fail 後重寫再送）
create table if not exists applications (
    id              bigserial primary key,
    contract_id     bigint not null references contracts(id) on delete cascade,
    attempt_no      int not null default 1,
    program         text,                              -- 送案別
    submitted_date  date,
    result          text not null default '審查中'
                    check (result in ('撰寫中','審查中','Pass','Fail','撤案')),
    result_date     date,
    approved_amount numeric,                           -- 過案（核定補助）金額
    writer          text,                              -- 這次的主寫
    note            text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    unique (contract_id, attempt_no)
);
create index if not exists applications_contract_idx on applications (contract_id);

-- 每期款項：前金1、前金2、期中、期末
create table if not exists payments (
    id              bigserial primary key,
    contract_id     bigint not null references contracts(id) on delete cascade,
    stage           text not null check (stage in ('前金1','前金2','期中','期末','其他')),
    grant_amount    numeric,                           -- 補助款撥款金額（期中／期末）
    invoice_amount  numeric,                           -- 我方發票金額
    invoice_date    date,
    invoice_no      text,
    received_date   date,
    is_received     boolean not null default false,   -- 舊資料常只寫「已收」沒有日期
    note            text,
    raw             jsonb not null default '{}',
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);
create index if not exists payments_contract_idx on payments (contract_id);
alter table payments add column if not exists is_received boolean not null default false;
alter table payments add column if not exists application_id bigint references applications(id) on delete set null;

-- 發票清單（原「109發票」～「115發票」、「發票_二聯」、「折讓」）：所有開出的發票，含非合約收入
create table if not exists invoices (
    id              bigserial primary key,
    invoice_no      text not null,
    company_id      bigint references companies(id) on delete set null,
    company_raw     text,
    payment_id      bigint references payments(id) on delete set null,  -- 對到合約款項
    invoice_date    date,
    amount_untaxed  numeric,
    amount_taxed    numeric,
    item            text,                              -- 個案簽約金／過案期中款／補助案尾款／訓練課程…
    paid_text       text,                              -- 付款日欄原文
    paid_date       date,
    is_received     boolean not null default false,
    is_void         boolean not null default false,
    allowance_amount numeric,                          -- 折讓金額（未稅）
    allowance_date  date,
    remarks         text,
    bonus_period    text,                              -- 115 1Q：獎金發放季
    extra           text,
    source          text not null default 'manual',
    source_sheet    text,
    source_row      int,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);
create index if not exists invoices_no_idx on invoices (invoice_no);
create index if not exists invoices_company_idx on invoices (company_id);
create index if not exists invoices_date_idx on invoices (invoice_date);

-- 合約／發票寄送與用印紀錄（原「合約追蹤」）
create table if not exists shipments (
    id              bigserial primary key,
    company_id      bigint references companies(id) on delete set null,
    contract_id     bigint references contracts(id) on delete set null,
    company_raw     text,
    contract_ref    text,                              -- 文字中找到的合約編號
    sales           text,
    method          text,                              -- 郵寄／面交／快遞
    party_a_stamp   boolean,                           -- 甲方用印
    our_stamp       boolean,                           -- 我方用印
    copies          text,
    sent_date       date,
    return_status   text,                              -- 寄回／面交日期欄（收到、待合約寄回、na/寄發票…）
    invoice_status  text,                              -- 發票已開出／發票待寄…
    purpose         text,                              -- 備註（SBIR簽約、北創第一期…）
    contact         text,
    phone           text,
    address         text,
    flag            text,                              -- Review／合作…
    raw             jsonb not null default '{}',
    source          text not null default 'manual',
    source_row      int,
    created_at      timestamptz not null default now()
);
create index if not exists shipments_company_idx on shipments (company_id);

-- ───────────────────────── 獎金與績效 ─────────────────────────
-- 獎金規則（每份合約各自設定，預設 5%／5%）：
--   主筆過案獎金 = 過案款發票（期中／期末／其他）× writer_bonus_pct → 主筆
--   獎金池       = 同一筆發票 × pool_pct
--   績效         = 績效基數（過案金額 × 抽成，可手動覆寫）× bonus_splits 各角色 %
alter table contracts add column if not exists main_writer       text;            -- 主筆（空白時用主寫）
alter table contracts add column if not exists writer_bonus_pct  numeric not null default 5;
alter table contracts add column if not exists pool_pct          numeric not null default 5;
alter table contracts add column if not exists performance_amount numeric;         -- 績效基數覆寫
alter table contracts add column if not exists performance_date  date;             -- 公文日

-- 績效分配：角色名稱與比例自由設定（業務 30／主寫 60／管考 10…）
create table if not exists bonus_splits (
    id          bigserial primary key,
    contract_id bigint not null references contracts(id) on delete cascade,
    scope       text not null default '績效',
    role        text not null,
    person      text not null,
    pct         numeric not null,
    note        text,
    source      text not null default 'manual',
    created_at  timestamptz not null default now()
);
alter table bonus_splits drop constraint if exists bonus_splits_scope_check;
alter table bonus_splits alter column scope set default '績效';
create index if not exists bonus_splits_contract_idx on bonus_splits (contract_id);
create index if not exists bonus_splits_person_idx on bonus_splits (person);

-- 獎金實際發放紀錄（主筆過案獎金、獎金池、其他）
create table if not exists bonus_disbursements (
    id          bigserial primary key,
    contract_id bigint references contracts(id) on delete cascade,
    payment_id  bigint references payments(id) on delete set null,
    kind        text not null,                        -- 主筆／獎金池／其他
    person      text,
    amount      numeric,
    paid_date   date,
    paid_text   text,
    note        text,
    source      text not null default 'manual',
    source_sheet text,
    source_row  int,
    created_at  timestamptz not null default now()
);
create index if not exists bonus_disb_contract_idx on bonus_disbursements (contract_id);
create index if not exists bonus_disb_payment_idx on bonus_disbursements (payment_id);

-- 每季個人獎金（原「績效報表」）
create table if not exists bonus_payouts (
    id          bigserial primary key,
    person      text not null,
    period      text not null,                        -- 2025 4Q
    category    text not null,                        -- 過案獎金／獎金池分配…
    amount      numeric,
    note        text,
    source      text not null default 'manual',
    source_sheet text,
    created_at  timestamptz not null default now()
);
create index if not exists bonus_payouts_person_idx on bonus_payouts (person);

-- ───────────────────────── 其他 ─────────────────────────
create table if not exists audit_log (
    id          bigserial primary key,
    username    text,
    action      text not null,
    table_name  text,
    record_id   bigint,
    detail      jsonb,
    created_at  timestamptz not null default now()
);

-- updated_at 自動更新
create or replace function touch_updated_at() returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end $$;
do $$ declare t text; begin
  foreach t in array array['companies','contracts','payments','applications','invoices'] loop
    execute format('drop trigger if exists %I_touch on %I', t, t);
    execute format('create trigger %I_touch before update on %I for each row execute function touch_updated_at()', t, t);
  end loop;
end $$;

-- 公司彙總（公司查詢頁使用）
create or replace view company_summary as
select c.id, c.tax_id, c.name, c.tax_id_status,
       count(distinct k.id)                                         as contract_count,
       count(distinct k.id) filter (where k.status ilike 'pass%')   as pass_count,
       max(k.start_date)                                            as last_contract_date,
       coalesce(sum(p.invoice_amount), 0)                           as invoiced_total,
       coalesce(sum(p.invoice_amount) filter (where p.is_received), 0) as received_total,
       coalesce(sum(p.invoice_amount) filter (where p.invoice_date is not null and not p.is_received), 0) as outstanding_total
from companies c
left join contracts k on k.company_id = c.id
left join payments  p on p.contract_id = k.id
group by c.id;

drop view if exists bonus_allocations;

-- 應發獎金：每筆已入帳的過案款 → 主筆、獎金池各一列，並標示是否已發
create or replace view bonus_due as
with base as (
    select p.id as payment_id, k.id as contract_id, k.contract_no, c.name as company, p.stage,
           coalesce(p.received_date, p.invoice_date) as received_date, p.invoice_amount,
           coalesce(nullif(k.main_writer,''), k.writer) as main_writer, k.writer_bonus_pct, k.pool_pct
    from payments p join contracts k on k.id = p.contract_id
    left join companies c on c.id = k.company_id
    where p.is_received and p.invoice_amount is not null and p.stage in ('期中','期末','其他')
)
select b.*, x.kind, x.person, x.pct, round(b.invoice_amount * x.pct / 100) as amount,
       d.paid_date, d.amount as paid_amount, (d.id is not null) as is_paid
from base b
cross join lateral (values ('主筆', b.main_writer, b.writer_bonus_pct),
                           ('獎金池', '獎金池', b.pool_pct)) as x(kind, person, pct)
left join lateral (select * from bonus_disbursements d
                   where d.payment_id = b.payment_id and d.kind = x.kind order by id limit 1) d on true
where x.pct > 0;

-- 績效：基數 × 各角色 %
create or replace view performance as
select k.id as contract_id, k.contract_no, c.name as company, k.contract_type, k.program,
       coalesce(k.performance_date, k.start_date) as perf_date,
       coalesce(k.performance_amount,
                k.approved_amount * nullif(substring(coalesce(k.final_rate, k.mid_rate) from '^\s*([0-9.]+)\s*%?\s*$'), '')::numeric / 100)
           as base,
       b.role, b.person, b.pct,
       round(coalesce(k.performance_amount,
                k.approved_amount * nullif(substring(coalesce(k.final_rate, k.mid_rate) from '^\s*([0-9.]+)\s*%?\s*$'), '')::numeric / 100)
             * b.pct / 100) as amount
from contracts k
left join companies c on c.id = k.company_id
join bonus_splits b on b.contract_id = k.id
where k.status ilike 'pass%';

-- 資料只透過 Streamlit 伺服器端連線存取：開啟 RLS 且不設任何 policy，
-- 讓 anon / authenticated 金鑰無法直接讀取（postgres 連線不受 RLS 影響）。
do $$ declare t text; begin
  foreach t in array array['companies','company_aliases','taxid_candidates','contacts','staff','app_users',
                           'contracts','applications','payments','invoices','shipments','bonus_splits','bonus_disbursements','bonus_payouts','audit_log'] loop
    execute format('alter table %I enable row level security', t);
  end loop;
end $$;
