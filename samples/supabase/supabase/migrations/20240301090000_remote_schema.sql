CREATE EXTENSION IF NOT EXISTS "pg_graphql" WITH SCHEMA "graphql";
COMMENT ON EXTENSION "pg_graphql" IS 'pg_graphql: GraphQL support';
CREATE EXTENSION IF NOT EXISTS "pgcrypto" WITH SCHEMA "extensions";
CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA "extensions";

create table public.profiles (
  id uuid not null references auth.users on delete cascade,
  username text unique,
  avatar_url text,
  updated_at timestamp with time zone,
  primary key (id),
  constraint username_length check (char_length(username) >= 3)
);
comment on table public.profiles is '회원 프로필';

alter table public.profiles enable row level security;
create policy "Public profiles are viewable by everyone." on public.profiles for select using (true);
create policy "Users can update own profile." on public.profiles for update using ((select auth.uid()) = id);

create function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = ''
as $$
begin
  insert into public.profiles (id, avatar_url)
  values (new.id, new.raw_user_meta_data->>'avatar_url');
  return new;
end;
$$;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

insert into storage.buckets (id, name) values ('avatars', 'avatars');
create policy "Avatar images are publicly accessible." on storage.objects for select using (bucket_id = 'avatars');
create policy "Users upload own avatar." on storage.objects for insert to authenticated
  with check (bucket_id = 'avatars' and (storage.foldername(name))[1] = (select auth.uid()::text));
