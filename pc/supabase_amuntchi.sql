-- =====================================================================
-- AMUNTCHI - Base Cloud de synchronisation PC / Android (Supabase)
-- A executer UNE SEULE FOIS : Supabase > SQL Editor > New query > coller > Run
-- =====================================================================

-- Une seule table recoit tous les enregistrements des deux applications.
create table if not exists public.amuntchi_records (
  tbl        text    not null,          -- nom de la table (products, sales, ...)
  id         bigint  not null,          -- identifiant de l'enregistrement
  data       jsonb,                     -- contenu de l'enregistrement
  deleted    boolean not null default false,
  device     text,                      -- appareil d'origine (PC, TEL-1, ...)
  changed_at text,                      -- horodatage UTC de la modification
  seq        bigint,                    -- ordre d'arrivee dans le Cloud
  primary key (tbl, id)
);

create sequence if not exists public.amuntchi_seq;

-- Chaque ecriture recoit un numero d'ordre croissant (synchronisation incrementale).
create or replace function public.amuntchi_set_seq()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  new.seq := nextval('public.amuntchi_seq');
  return new;
end;
$$;

drop trigger if exists amuntchi_seq_trg on public.amuntchi_records;
create trigger amuntchi_seq_trg
  before insert or update on public.amuntchi_records
  for each row execute function public.amuntchi_set_seq();

create index if not exists amuntchi_records_seq_idx on public.amuntchi_records (seq);

-- Securite : seul le compte de la boutique (connecte) peut lire et ecrire.
alter table public.amuntchi_records enable row level security;

drop policy if exists "amuntchi_compte_boutique" on public.amuntchi_records;
create policy "amuntchi_compte_boutique" on public.amuntchi_records
  for all to authenticated
  using (true) with check (true);

grant select, insert, update, delete on public.amuntchi_records to authenticated;
revoke all on public.amuntchi_records from anon;
