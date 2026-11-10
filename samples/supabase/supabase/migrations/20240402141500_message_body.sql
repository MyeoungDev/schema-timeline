alter table public.messages rename column content to body;
alter table public.messages add column edited_at timestamptz;
create index messages_channel_id_created_at_idx on public.messages (channel_id, created_at desc);
alter table public.profiles add column full_name text;
