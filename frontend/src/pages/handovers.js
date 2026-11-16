import {handovers} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {esc,date,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';

const canInitiate=()=>session.user.role==='analyst';

export async function handoverPage(traceBatch){
 const batch=traceBatch?decodeURIComponent(traceBatch):'';
 const [list,ctx]=await Promise.all([handovers.list(),handovers.context()]);workspace.handovers=list;
 let trace=null;
 if(batch)trace=await handovers.trace(batch).catch(()=>null);
 const initiatePanel=canInitiate()?'<form id="handover-form"><label>批号<input name="batch_no" required minlength="2" placeholder="须与已接入数据批次名称一致，例如 HP-R001 复检留样批"></label><div class="parameter-grid"><label>交出保管库<select name="from_library">'+ctx.libraries.map(l=>'<option>'+esc(l)+'</option>').join('')+'</select></label><label>接收保管库<select name="to_library">'+ctx.libraries.map(l=>'<option>'+esc(l)+'</option>').join('')+'</select></label></div><div class="parameter-grid"><label>应交数量<input type="number" name="sample_count" min="1" max="10000" value="1" required></label><label>指定接收员<select name="receiver_id">'+ctx.receivers.filter(r=>r.email!==session.user.email).map(r=>'<option value="'+r.id+'">'+esc(r.name)+' · '+esc(r.email)+'</option>').join('')+'</select></label></div><label>交接说明<input name="note" maxlength="500" placeholder="留样标签、封条与交接清单一并转交"></label><button type="submit" class="primary">整单发起，样品在途 ↗</button><p class="hint">发起后只有该单指定接收员可以核对签收；发起人可在签收前撤回。在途或争议期间该批号不能发起新分析。</p></form>':(session.user.role==='viewer'?'<p class="empty">观察员只可查看交接记录与去向。</p>':'<p class="empty">负责人不参与实物交出与签收，只在差异转争议后裁定。</p>');
 return heading('04 / CUSTODY CHAIN','样品跨库交接链','整单发起即在途，指定接收员核对实物后整单签收；数量不符转争议，由交接双方之外的负责人裁定。')+
 '<div class="handover-grid"><section class="panel"><div class="section-title"><h2>发起交接单</h2><span>整单登记 · 批号唯一占用</span></div>'+
 initiatePanel+
 '<div class="section-title"><h2>当前保管归属</h2><span>'+ctx.custody.length+' 个批号</span></div>'+
 (ctx.custody.length?'<div class="custody-list">'+ctx.custody.map(c=>'<div class="custody-row"><strong>'+esc(c.batch_no)+'</strong><span>'+esc(c.library)+'</span><small>'+esc(c.holder_name||'未登记持有人')+(c.handover_id?' · 交接单 #'+c.handover_id+' 占用中':'')+'</small></div>').join('')+'</div>':'<p class="empty">尚无保管登记。发起首张交接单后在此显示批号归属。</p>')+
 '</section><section class="panel"><div class="section-title"><h2>交接单与去向</h2><span>'+list.length+' 张</span></div><form id="trace-form" class="trace-form"><label>按批号追查<input name="batch_no" placeholder="输入批号查看完整交接链" value="'+esc(batch)+'" required></label><button type="submit" class="button small">追查去向</button></form>'+
 (trace?traceBlock(trace):'<p class="hint">追查会显示当前保管库、在途单与每张单的操作人、时间与原因。</p>')+
 '<div class="handover-list">'+(list.length?list.map(o=>card(o)).join(''):'<p class="empty">还没有交接单。</p>')+'</div></section></div>';
}

function traceBlock(t){
 const custody=t.custody?t.custody.library+' · '+(t.custody.holder_name||'未登记持有人'):'无保管登记';
 return '<div class="trace-result"><p class="eyebrow">TRACE / '+esc(t.batch_no)+'</p><h3>当前保管：'+esc(custody)+'</h3>'+
 (t.active?'<p class="warning">该批号存在'+esc(t.active.status_label)+'交接单 '+esc(t.active.order_no)+'，期间禁止发起新分析。</p>':'<p class="hint">该批号当前没有在途或争议交接单。</p>')+
 t.handovers.map(o=>'<details class="trace-order" '+(o.id===t.handovers[0]?.id?'open':'')+'><summary>'+esc(o.order_no)+' · '+esc(o.status_label)+' · '+esc(o.initiator.name)+' → '+esc(o.receiver.name)+'</summary><ol class="chain">'+o.events.map(e=>'<li><strong>'+esc(eventLabel(e.action))+'</strong><span>'+esc(e.actor_name||e.actor_email)+' · '+date(e.at)+'</span>'+(e.detail?.note||e.detail?.reason?'<small>'+esc(e.detail.note||e.detail.reason)+'</small>':'')+'</li>').join('')+'</ol></details>').join('')+'</div>';
}

function eventLabel(action){return {initiated:'发起交接',signed:'核对签收',withdrawn:'发起人撤回',disputed:'差异转争议',resolved:'负责人裁定'}[action]||action;}

function card(o){
 const actions=[];
 if(o.can_sign)actions.push('<details class="action"><summary class="button small">核对签收</summary><form data-sign="'+o.id+'"><label>实收数量<input type="number" name="received_count" min="0" max="10000" value="'+o.sample_count+'" required></label><label>差异说明（数量不符时必填）<textarea name="difference_note" rows="2" maxlength="500"></textarea></label><button type="submit" class="primary small">整单签收</button></form></details>');
 if(o.can_withdraw)actions.push('<details class="action"><summary class="button small">撤回</summary><form data-withdraw="'+o.id+'"><label>撤回原因<input name="reason" maxlength="500" placeholder="复检排期调整"></label><button type="submit" class="small">撤回未签收单</button></form></details>');
 if(o.can_resolve)actions.push('<details class="action"><summary class="button small">裁定争议</summary><form data-resolve="'+o.id+'"><label>裁定结果<select name="resolution"><option value="accept">接收方留样</option><option value="return">退回交出方</option></select></label><label>裁定原因<textarea name="reason" rows="2" minlength="5" maxlength="1000" required placeholder="核对封条与清单后说明责任与去向"></textarea></label><button type="submit" class="primary small">提交裁定</button></form></details>');
 let extra='';
 if(o.status==='dispute')extra='<p class="warning">差异：应交 '+o.sample_count+' · 实收 '+o.received_count+'。'+esc(o.difference_note)+'</p>';
 if(o.status==='resolved')extra='<p class="hint">裁定：'+esc(o.resolution_label)+' · '+esc(o.resolution_reason)+'（'+esc(o.resolved_by)+'）</p>';
 if(o.status==='withdrawn'&&o.withdraw_reason)extra='<p class="hint">撤回原因：'+esc(o.withdraw_reason)+'</p>';
 return '<article class="handover-card"><div class="handover-head"><div><span class="eyebrow">'+esc(o.order_no)+'</span><strong>'+esc(o.batch_no)+'</strong><p>'+esc(o.from_library)+' → '+esc(o.to_library)+' · '+o.sample_count+' 支</p></div><span class="status '+esc(o.status)+'">'+esc(o.status_label)+'</span></div><div class="handover-people"><span>交出 '+esc(o.initiator.name)+'</span><span>接收 '+esc(o.receiver.name)+'</span><span>'+date(o.created_at)+'</span>'+(o.status==='signed'?'<span>签收 '+date(o.signed_at)+'</span>':'')+'</div>'+extra+(o.note?'<p class="hint">'+esc(o.note)+'</p>':'')+(actions.length?'<div class="handover-actions">'+actions.join('')+'</div>':'')+'</article>';
}

export function bindHandovers(refresh){
 document.querySelector('#trace-form')?.addEventListener('submit',e=>{e.preventDefault();const b=new FormData(e.target).get('batch_no');location.hash='#/handovers/'+encodeURIComponent(String(b).trim());});
 submit(document.querySelector('#handover-form'),async f=>{const o=await handovers.initiate({batch_no:f.get('batch_no'),from_library:f.get('from_library'),to_library:f.get('to_library'),sample_count:Number(f.get('sample_count')),receiver_id:Number(f.get('receiver_id')),note:f.get('note')||''});notify('交接单 '+o.order_no+' 已发起，样品在途');await refresh();});
 document.querySelectorAll('[data-sign]').forEach(form=>submit(form,async f=>{const o=await handovers.sign(form.dataset.sign,{received_count:Number(f.get('received_count')),difference_note:f.get('difference_note')||''});notify(o.status==='dispute'?'数量不符，已转入争议':'已核对签收，保管归属已转移');await refresh();}));
 document.querySelectorAll('[data-withdraw]').forEach(form=>submit(form,async f=>{await handovers.withdraw(form.dataset.withdraw,{reason:f.get('reason')||''});notify('已撤回未签收单');await refresh();}));
 document.querySelectorAll('[data-resolve]').forEach(form=>submit(form,async f=>{await handovers.resolve(form.dataset.resolve,{resolution:f.get('resolution'),reason:f.get('reason')});notify('争议已裁定并记录原因');await refresh();}));
}
