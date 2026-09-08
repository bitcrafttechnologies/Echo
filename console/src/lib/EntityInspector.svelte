<script lang="ts">
  import { onMount } from 'svelte';
  import { fetchEntities, fetchEntity, fetchRelationships, fetchTasks, type EntityInspection, type RelationshipState, type TaskHistoryEntry } from '$lib/echo-client';
  export let apiBase: string;
  export let eventRevision = 0;
  let entities: EntityInspection[]=[]; let selected:EntityInspection|null=null; let relationships:RelationshipState[]=[]; let tasks:TaskHistoryEntry[]=[]; let loading=true; let error=''; let mounted=false; let seen=-1;
  $: activeTasks = selected ? tasks.filter((task)=>selected?.active_task_ids.includes(task.id)) : [];
  $: if(mounted && eventRevision!==seen){seen=eventRevision; void load(false)}
  onMount(()=>{mounted=true;void load()});
  async function load(show=true){if(show)loading=true;try{entities=await fetchEntities(window.fetch.bind(window),apiBase);const id=selected?.id??entities[0]?.id;if(id) await select(id);else selected=null;error=''}catch{error='Entity inspection is unavailable.'}finally{loading=false}}
  async function select(id:string){const [entity,nextRelationships,nextTasks]=await Promise.all([fetchEntity(window.fetch.bind(window),id,apiBase),fetchRelationships(window.fetch.bind(window),id,apiBase),fetchTasks(window.fetch.bind(window),apiBase,{limit:200})]);selected=entity;relationships=nextRelationships;tasks=nextTasks}
  const pretty=(value:unknown)=>JSON.stringify(value,null,2);
</script>
<section class="inspector" aria-labelledby="entity-heading">
  <header><div><p>Entity inspector</p><h2 id="entity-heading">Actors & social state</h2></div><button onclick={()=>load()} disabled={loading} type="button">{loading?'Loading…':'Refresh'}</button></header>
  {#if error}<div class="error" role="status">{error}</div>{/if}
  <div class="grid">
    <aside class="entities"><h3>Entities</h3>{#if entities.length}<nav>{#each entities as entity}<button class:selected={selected?.id===entity.id} onclick={()=>select(entity.id)} type="button"><strong>{entity.id}</strong><span>{entity.handlers.count} handlers · {entity.active_task_ids.length} active</span></button>{/each}</nav>{:else}<p class="empty">No Entities are registered.</p>{/if}</aside>
    <section class="detail">
      {#if selected}
        <div class="title"><div><span>Entity ID</span><h3>{selected.id}</h3></div><span>{selected.active_task_ids.length} active Tasks</span></div>
        <div class="cards">
          <section><h4>Current state</h4><pre>{pretty(selected.state)}</pre></section>
          <section><h4>Active Tasks</h4>{#if activeTasks.length}<ul>{#each activeTasks as task}<li><strong>{task.name}</strong><span>{task.status} · {task.id}</span></li>{/each}</ul>{:else}<p class="empty">No active work.</p>{/if}</section>
        </div>
        <section class="character">
          <h4>Character state</h4>
          <div class="character-grid">
            <article><span>Identity</span><pre>{pretty(selected.character.identity ?? {})}</pre></article>
            <article><span>Traits</span><pre>{pretty(selected.character.traits ?? {})}</pre></article>
            <article><span>Control state</span><pre>{pretty(selected.character.internal_state ?? {})}</pre></article>
            <article><span>Drives</span><pre>{pretty(selected.character.drives ?? {})}</pre></article>
            <article><span>Self-model & embodiment</span><pre>{pretty(selected.character.self_model ?? {})}</pre></article>
            <article><span>Attention</span><pre>{pretty(selected.character.attention_candidates ?? [])}</pre></article>
          </div>
        </section>
        <section class="handlers"><h4>Registered handlers</h4>{#if selected.handlers.registrations.length}<div class="table">{#each selected.handlers.registrations as handler}<div><code>{handler.signal}</code><span>{handler.signal_kind}</span><strong>{handler.handler}</strong></div>{/each}</div>{:else}<p class="empty">No handlers registered.</p>{/if}</section>
        <section class="social"><div class="social-title"><div><span>Character / relationships</span><h4>Per-person context</h4></div><strong>{relationships.length}</strong></div>{#if relationships.length}<div class="relationship-grid">{#each relationships as relationship}<article><div><h5>{relationship.subject_id}</h5><span>{relationship.interaction_count} interactions</span></div><dl><div><dt>Familiarity</dt><dd>{Math.round(relationship.familiarity*100)}%</dd></div><div><dt>Trust</dt><dd>{Math.round(relationship.trust*100)}%</dd></div></dl><p><b>Interests</b> {relationship.known_interests.join(', ')||'None recorded'}</p><p><b>Preferences</b> {pretty(relationship.communication_preferences)}</p><p><b>Current context</b> {pretty(relationship.current_context)}</p><p><b>Boundaries</b> {relationship.boundaries.join(', ')||'None recorded'}</p></article>{/each}</div>{:else}<p class="empty">No per-person relationship context is registered.</p>{/if}</section>
      {:else}<p class="empty">Select an Entity to inspect it.</p>{/if}
    </section>
  </div>
</section>
<style>
  .inspector{width:min(100%,86rem);margin:0 auto}header,.title,.social-title,.relationship-grid article>div,.relationship-grid dl{display:flex;align-items:center;justify-content:space-between;gap:1rem}header{margin-bottom:1.4rem}h2,h3,h4,h5,p{margin:0}h2{font-size:clamp(1.75rem,3vw,2.5rem)}h3{font-size:1rem}h4{font-size:.9rem}h5{font-size:.95rem}header p,.title span,.social-title span{color:#6f8093;font:.75rem ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase;margin-bottom:.4rem}button{border:1px solid #314153;border-radius:.4rem;color:#dce6ef;background:#14202c;padding:.6rem .85rem;cursor:pointer}button:hover,button:focus-visible{border-color:#4ccbc5;outline:none}.error{padding:.8rem;border:1px solid #5d3038;color:#efb1b6;background:#251218}.grid{display:grid;grid-template-columns:16rem 1fr;border:1px solid #1d2a38;border-radius:.6rem;overflow:hidden;background:#0d141d}.entities{border-right:1px solid #1d2a38}.entities h3{padding:1rem;border-bottom:1px solid #1d2a38}.entities nav{padding:.4rem;display:grid;gap:.25rem}.entities nav button{display:grid;gap:.35rem;text-align:left;border-color:transparent;background:transparent}.entities nav button.selected{border-color:#24676b;background:#10252c}.entities nav span{color:#748397;font-size:.75rem}.detail{min-width:0}.title{padding:1rem 1.2rem;border-bottom:1px solid #1d2a38}.title h3{font:1rem ui-monospace,monospace}.title>span{color:#58d68d}.cards{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:#1d2a38}.cards section,.character,.handlers,.social{padding:1.1rem;background:#101821}.cards h4,.character>h4,.handlers h4{margin-bottom:.7rem}pre{min-height:6rem;overflow:auto;margin:0;padding:.8rem;border:1px solid #223142;border-radius:.35rem;color:#a9c2c8;background:#091019;font:.75rem/1.5 ui-monospace,monospace}.character,.handlers,.social{border-top:1px solid #1d2a38}.character-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.7rem}.character-grid article{min-width:0}.character-grid article>span{display:block;margin-bottom:.4rem;color:#718196;font-size:.75rem}.character-grid pre{max-height:12rem}ul{list-style:none;margin:0;padding:0;display:grid;gap:.45rem}li{display:grid;gap:.25rem;padding:.65rem;border:1px solid #263342;border-radius:.35rem}li span{color:#78879a;font:.73rem ui-monospace,monospace}.table{display:grid}.table>div{display:grid;grid-template-columns:minmax(8rem,.6fr) 5rem 1fr;gap:1rem;padding:.7rem 0;border-top:1px solid #1c2a38;align-items:center}.table span{color:#728195;font-size:.75rem}.table strong{font-size:.8rem;overflow-wrap:anywhere}.social-title{margin-bottom:.8rem}.social-title strong{color:#57d7ce}.relationship-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(16rem,1fr));gap:.7rem}.relationship-grid article{padding:1rem;border:1px solid #263342;border-radius:.45rem;background:#0b121a}.relationship-grid article>div span{color:#728195;font-size:.75rem}.relationship-grid dl{justify-content:flex-start;margin:.9rem 0}.relationship-grid dt{color:#657487;font-size:.7rem;text-transform:uppercase}.relationship-grid dd{margin:.2rem 0 0;color:#57d7ce}.relationship-grid p{margin-top:.5rem;color:#91a0b1;font:.75rem/1.45 ui-monospace,monospace;white-space:pre-wrap}.relationship-grid b{color:#c3cfdb}.empty{padding:1.2rem;color:#657487;font-size:.86rem}@media(max-width:1050px){.character-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:800px){.grid{grid-template-columns:1fr}.entities{border:0;border-bottom:1px solid #1d2a38}.entities nav{grid-template-columns:repeat(auto-fit,minmax(10rem,1fr))}.cards,.character-grid{grid-template-columns:1fr}.table>div{grid-template-columns:1fr}.table span{display:none}}
</style>
