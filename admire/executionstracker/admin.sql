create table if not exists executions (
    message_id bigint not null constraint executions_pk primary key,
    created_at timestamp not null,
    guild_id bigint,
    guild_name text,
    channel_id bigint not null,
    channel_name text,
    user_id bigint not null,
    user_name bigint not null,
    message text,
    invoked_with text,
    failed boolean,
    prefix text,
    subcommand text,
    args text,
    command text,
    error text,
    bot_user text not null
);

alter table
    executions owner to melanie;

create index if not exists executions_guild_id_index on executions (guild_id);

create index if not exists executions_user_id_index on executions (user_id);