(function () {
  const $=(s)=>container.querySelector(s);
  const statusEl=$('#ks-status'), errorEl=$('#ks-error'), emptyEl=$('#ks-empty'), listEl=$('#ks-list'), paginationEl=$('#ks-pagination'), searchEl=$('#ks-search'), requesterEl=$('#ks-requester'), subtagsEl=$('#ks-subtags'), countsEl=$('#ks-counts'), pageInfoEl=$('#ks-page-info'), queueEl=$('#ks-queue'), currentEl=$('#ks-current'), nowTitle=$('#ks-now-title'), nowMeta=$('#ks-now-meta'), nowThumb=$('#ks-now-thumb'), progressEl=$('#ks-progress'), nowTime=$('#ks-now-time'), ytWrap=$('#ks-yt-wrap');
  const KOREAN_LABELS={'ㄱ':'가','ㄴ':'나','ㄷ':'다','ㄹ':'라','ㅁ':'마','ㅂ':'바','ㅅ':'사','ㅇ':'아','ㅈ':'자','ㅊ':'차','ㅋ':'카','ㅌ':'타','ㅍ':'파','ㅎ':'하'};
  const FAV_KEY='ks-favorites';
  function loadFavorites(){try{return new Set(JSON.parse(localStorage.getItem(FAV_KEY)||'[]'))}catch(_){return new Set()}}
  function saveFavorites(){localStorage.setItem(FAV_KEY,JSON.stringify([...state.favorites]))}
  function songKey(song){return (song.brand||'')+':'+(song.no||'')}
  function toggleFavorite(song){const key=songKey(song);if(state.favorites.has(key))state.favorites.delete(key);else state.favorites.add(key);saveFavorites();renderSongs()}
  const state={brand:'all',category:'all',letter:'all',page:1,pageSize:30,total:0,pages:1,songs:[],keys:{},videos:[],videoIndex:0,videoAttemptToken:0,mrCandidates:[],mrResult:null,mediaMode:'youtube',mediaElement:null,player:null,current:null,startedId:null,tvOpen:false,ytReady:false,lastDuration:0,lastSeconds:0,pollBusy:false,reserving:false,playToken:0,previewMode:false,favoritesOnly:false,favorites:loadFavorites(),speed:1,mode:'list',tjChartType:'TOP',kyChartPeriod:''};
  state.waitingCount=0;state.stoppingCurrent=false;state.historyRevision=0;
  function showError(t){errorEl.hidden=!t;errorEl.textContent=t||''}
  function apiUrl(params){return '/api/media/dashboard/widgets/'+encodeURIComponent(pluginId)+'/data?'+new URLSearchParams(Object.assign({type:'general',limit:String(state.pageSize),_ts:String(Date.now())},params||{})).toString()}
  async function api(params){try{const r=await fetch(apiUrl(params),{cache:'no-store',credentials:'same-origin'});const d=await r.json();if(!r.ok)d.success=false;return d}catch(e){return {success:false,error:e.message||'서버 연결 실패'}}}
  function pill(t,ok){if(statusEl){statusEl.textContent=t||'';statusEl.classList.toggle('is-ok',ok===true);statusEl.classList.toggle('is-bad',ok===false)}}
  function esc(v){const d=document.createElement('div');d.textContent=v==null?'':String(v);return d.innerHTML}
  function letterLabel(c,k){return c==='korean'?(KOREAN_LABELS[k]||k):c==='special'?'기타':k}
  function renderSubtags(){if(state.category==='all'){subtagsEl.hidden=true;subtagsEl.innerHTML='';return}const keys=state.keys[state.category]||[];subtagsEl.innerHTML='';keys.forEach(k=>{const b=document.createElement('button');b.className='ks-tag'+(state.letter===k?' is-active':'');b.textContent=letterLabel(state.category,k);b.onclick=()=>{stopPreviewIfActive();state.letter=state.letter===k?'all':k;state.page=1;refresh()};subtagsEl.appendChild(b)});subtagsEl.hidden=!keys.length}
  function renderSongs(){listEl.innerHTML='';const isOfficialChart=state.mode==='tjchart'||state.mode==='kychart';(state.songs||[]).forEach(song=>{const row=document.createElement('article');row.className='ks-row';const badgeText=isOfficialChart&&song.rank?(song.rank+'위'):(song.brand_label||song.brand||'').toUpperCase();const favKey=songKey(song);const isFav=state.favorites.has(favKey);const metaLine=isOfficialChart?esc(song.singer||''):(song._meta?(esc(song.singer||'')+(song.singer?' · ':'')+esc(song._meta)):esc(song.singer||''));row.innerHTML='<div class="ks-song-no"><span class="ks-brand-mini '+(song.brand==='tj'?'tj':'ky')+'">'+esc(badgeText)+'</span><b>'+esc(song.no||'')+'</b></div><div class="ks-song-main"><h3><button class="ks-song-fav'+(isFav?' is-active':'')+'" title="즐겨찾기"><i class="fa-solid fa-star"></i></button>'+esc(song.title||'제목 없음')+'</h3><p>'+metaLine+'</p></div><div class="ks-row-actions"><button class="ks-btn ks-reserve"><i class="fa-solid fa-music"></i> 예약</button><button class="ks-btn ks-btn-ghost ks-mr"><i class="fa-brands fa-youtube"></i> MR</button><button class="ks-icon-btn ks-mr-pin" title="이 곡에 사용할 유튜브 URL 직접 지정"><i class="fa-solid fa-link"></i></button></div>';row.querySelector('.ks-reserve').onclick=()=>reserve(song);row.querySelector('.ks-mr').onclick=()=>playMr(song);row.querySelector('.ks-mr-pin').onclick=()=>pinManualMr(song);row.querySelector('.ks-song-fav').onclick=()=>toggleFavorite(song);listEl.appendChild(row)});emptyEl.hidden=state.total!==0}
  function renderPagination(){paginationEl.innerHTML='';if(state.pages<=1)return;const make=(text,page,active=false,disabled=false)=>{const b=document.createElement('button');b.className='ks-page'+(active?' is-active':'');b.textContent=text;b.disabled=disabled;b.onclick=()=>{if(!disabled){stopPreviewIfActive();state.page=page;refresh()}};return b};paginationEl.appendChild(make('‹',Math.max(1,state.page-1),false,state.page===1));let start=Math.max(1,state.page-3),end=Math.min(state.pages,start+6);start=Math.max(1,end-6);if(start>1){paginationEl.appendChild(make('1',1));if(start>2){const s=document.createElement('span');s.textContent='…';paginationEl.appendChild(s)}}for(let i=start;i<=end;i++)paginationEl.appendChild(make(String(i),i,i===state.page));if(end<state.pages){if(end<state.pages-1){const s=document.createElement('span');s.textContent='…';paginationEl.appendChild(s)}paginationEl.appendChild(make(String(state.pages),state.pages))}paginationEl.appendChild(make('›',Math.min(state.pages,state.page+1),false,state.page===state.pages))}
  function renderCounts(d){const c=d.counts||{};$('#ks-count-main').textContent=Number(c.all||0).toLocaleString();countsEl.textContent='전체 '+Number(c.all||0).toLocaleString()+' · TJ '+Number(c.tj||0).toLocaleString()+' · 금영 '+Number(c.kumyoung||0).toLocaleString();pill(Number(c.all||0).toLocaleString()+'곡',true);pageInfoEl.textContent=state.total?state.page+' / '+state.pages+' 페이지 · '+Number(state.total).toLocaleString()+'곡':''}
  async function refresh(){state.mode='list';setListHead('fa-solid fa-music',state.favoritesOnly?'즐겨찾기':'노래 목록',state.favoritesOnly?'즐겨찾기한 곡만 표시 중입니다.':'예약 버튼을 누르면 대기열에 등록됩니다.',false);showError('');const params={view:'list',q:searchEl.value||'',brand:state.brand,category:state.category,letter:state.letter,offset:String((state.page-1)*state.pageSize),limit:String(state.pageSize)};if(state.favoritesOnly){params.favorites_only='1';params.favorites=[...state.favorites].join(',')}const d=await api(params);if(!d.success){showError(d.error||'목록을 불러오지 못했습니다.');return}state.keys=d.keys||{};state.total=d.total||0;
    // 서버가 total은 맞게 주더라도 pages 값이 어긋나는 경우(예: 프레임워크/캐시 문제)에 대비해,
    // 페이지 수는 total과 pageSize로 클라이언트에서 다시 계산해 항상 정확하게 표시한다.
    state.pages=Math.max(1,Math.ceil(state.total/state.pageSize));
    if(state.page>state.pages)state.page=state.pages;
    // 서버가 실수로 한 페이지 분량보다 많이 내려주더라도 화면에는 pageSize만큼만 렌더링한다.
    state.songs=(d.songs||[]).slice(0,state.pageSize);renderCounts(d);renderSubtags();renderSongs();renderPagination()}
  function queueSource(d){return d||{}}
  function renderQueue(d){
    const src=queueSource(d),q=Array.isArray(src.queue)?src.queue:[],cur=src.current||null;
    state.current=cur||null;
    const waiting=q.filter(x=>x.status==='waiting');
    state.waitingCount=waiting.length;
    $('#ks-queue-badge').textContent=waiting.length;
    $('#ks-queue-count').textContent=waiting.length;
    currentEl.classList.toggle('ks-current--active',!!cur);
    currentEl.innerHTML=cur
      ?'<div class="ks-current-main"><b>▶ '+esc(cur.title)+'</b><span>'+esc(cur.singer||'')+(cur.requester?' · '+esc(cur.requester):'')+'</span></div><button type="button" class="ks-icon-btn ks-current-stop" title="현재 곡 중단" aria-label="현재 곡 중단"><i class="fa-solid fa-stop"></i></button>'
      :'<span>현재 재생 없음</span>';
    const currentStop=currentEl.querySelector('.ks-current-stop');
    if(currentStop)currentStop.onclick=stopCurrent;
    queueEl.innerHTML='';
    waiting.forEach((x,i)=>{
      const r=document.createElement('div');
      r.className='ks-qrow';r.style.cursor='pointer';r.title='클릭하면 이 곡을 바로 재생합니다';
      r.innerHTML='<span class="ks-qnum">'+(i+1)+'</span><span><b>'+esc(x.title)+'</b><small>'+esc(x.singer||'')+(x.requester?' · '+esc(x.requester):'')+'</small></span><button class="ks-icon-btn" title="예약 취소"><i class="fa-solid fa-xmark"></i></button>';
      r.querySelector('button').onclick=(e)=>{e.stopPropagation();removeQueue(x.id)};
      r.onclick=()=>playNow(x.id);
      queueEl.appendChild(r)
    });
    renderTvQueue(waiting);
    if(cur)updateNow(cur);
    const summary=src.summary||{};
    const mine=(requesterEl.value||localStorage.getItem('ks-requester')||'').trim();
    const mineCount=(summary.requesters||[]).find(x=>String(x.requester||'')===mine)?.waiting_count||0;
    const mineEl=$('#ks-my-queue');
    if(mineEl)mineEl.textContent=mine?('내 대기 '+mineCount+'곡'):'예약자 이름을 입력하세요';
    updateResumeAffordance()
  }
  function renderTvQueue(waiting){const el=$('#ks-tv-queue');if(!el)return;el.innerHTML=waiting.slice(0,5).map((x,i)=>'<span>'+(i+1)+' '+esc(x.title)+'</span>').join('')}
  async function loadQueue(){if(!isPluginViewActive())return;const d=await api({view:'queue'});if(isPluginViewActive()&&d.success)renderQueue(d)}
  async function reserve(song){showError('');if(state.reserving)return;const requester=(requesterEl.value||localStorage.getItem('ks-requester')||'').trim();if(!requester){showError('예약자 이름을 먼저 입력해주세요.');requesterEl.focus();return}state.reserving=true;try{localStorage.setItem('ks-requester',requester);const d=await api({view:'queue_add',song:JSON.stringify(song),requester});if(!d.success){showError(d.error||'예약에 실패했습니다.');return}pill(d.duplicate?'이미 예약된 곡입니다.':'예약 완료'+(d.autostarted?' · 재생 시작':''),!d.duplicate);renderQueue(d);if(d.current&&d.autostarted)await playQueued(d.current)}finally{state.reserving=false}}
  async function removeQueue(id){const d=await api({view:'queue_remove',id:String(id)});if(!d.success)showError(d.error||'예약 취소 실패');else pill('예약을 취소했습니다.',true);renderQueue(d)}
  async function nextQueue(){const d=await api({view:'queue_next'});if(!d.success){showError(d.error||'다음곡으로 이동하지 못했습니다.');return}renderQueue(d);if(d.current){await playQueued(d.current)}else stopPlayer()}
  async function clearQueue(){if(!confirm('대기 중인 예약곡을 모두 삭제할까요?'))return;const d=await api({view:'queue_clear'});renderQueue(d);pill('대기열을 비웠습니다.',true)}
  function ensureYt(cb){if(window.YT&&window.YT.Player){state.ytReady=true;cb();return}const prev=window.onYouTubeIframeAPIReady;window.onYouTubeIframeAPIReady=()=>{if(typeof prev==='function')prev();state.ytReady=true;cb()};if(!document.querySelector('script[src*="youtube.com/iframe_api"]')){const tag=document.createElement('script');tag.src='https://www.youtube.com/iframe_api';document.head.appendChild(tag)}}
  function setNowThumbnail(video){if(video&&video.id){nowThumb.innerHTML='<img src="https://i.ytimg.com/vi/'+encodeURIComponent(video.id)+'/mqdefault.jpg" alt=""><div><i class="fa-solid fa-play"></i></div>'}else nowThumb.innerHTML='<div><i class="fa-solid fa-music"></i></div>'}
  function syncProgress(){const p=state.mediaElement||state.player;if(!p)return;try{let sec=0,dur=0;if(state.mediaMode==='youtube'&&p.getCurrentTime){sec=p.getCurrentTime()||0;dur=p.getDuration()||0}else if(p.currentTime!==undefined){sec=p.currentTime||0;dur=p.duration||0}state.lastSeconds=sec;state.lastDuration=dur;progressEl.style.width=(dur?Math.min(100,sec/dur*100):0)+'%';nowTime.textContent=formatTime(sec)+' / '+formatTime(dur)}catch(e){}}
  function formatTime(sec){sec=Math.max(0,Math.floor(Number(sec)||0));return String(Math.floor(sec/60)).padStart(2,'0')+':'+String(sec%60).padStart(2,'0')}
  function makePlayer(host,videoId,events){host.innerHTML='';const player=new YT.Player(host,{videoId,width:'100%',height:'100%',playerVars:{autoplay:1,controls:1,rel:0,playsinline:1,modestbranding:1,origin:location.origin},events});setTimeout(()=>{try{const iframe=player.getIframe();if(iframe){iframe.setAttribute('allow','autoplay; encrypted-media; picture-in-picture');iframe.style.width='100%';iframe.style.height='100%'}}catch(_){}},300);return player}
  async function resolveMr(song,force=false){const d=await api({view:'mr_resolve',song:JSON.stringify(song),force:force?'1':'0'});if(!d.success){const trace=(d.trace||[]).map(x=>{const qs=(x.query_stats||[]).map(q=>q.query+' ['+(q.status||'')+', rc='+(q.returncode??'-')+', '+(q.results??0)+'건'+(q.stderr?' / '+q.stderr:'')+']').join(' | ');return x.step+': '+(x.message||x.status||'')+(qs?' / '+qs:'')}).join(' / ');showError((d.error||'MR 탐색에 실패했습니다.')+(trace?' ['+trace+']':''));pill('MR 탐색 실패',false);return null}const r=d.result||{};if(r.status!=='ok'){const trace=(d.trace||[]).map(x=>{const qs=(x.query_stats||[]).map(q=>q.query+' ['+(q.status||'')+', rc='+(q.returncode??'-')+', '+(q.results??0)+'건'+(q.stderr?' / '+q.stderr:'')+']').join(' | ');return x.step+': '+(x.message||x.status||'')+(qs?' / '+qs:'')}).join(' / ');showError((r.message||'MR을 찾지 못했습니다.')+(trace?' '+trace:''));pill('MR 탐색 실패',false);return null}state.mrResult=r;state.mrCandidates=(r.candidates||[]);pill('MR '+(r.source==='local'?'로컬 파일':r.source==='youtube-ytdlp'?'yt-dlp':'YouTube API')+' 선택',true);return r}
  function reportMrError(song,videoId,reason){if(!song||!videoId)return;try{api({view:'mr_report_error',song:JSON.stringify(song),video_id:videoId,reason:reason||'embed_disabled'})}catch(_){}}
  function playVideoCandidates(candidates,index,onEnd){
    const token=state.playToken;
    if(!candidates.length||index>=candidates.length){if(onEnd)onEnd();return}
    const attemptToken=++state.videoAttemptToken;
    const isCurrentAttempt=()=>token===state.playToken&&attemptToken===state.videoAttemptToken;
    let settled=false;
    const settle=()=>{
      if(!isCurrentAttempt()||settled)return false;
      settled=true;
      state.videoAttemptToken++;
      return true;
    };
    const finish=()=>{if(settle()&&onEnd)onEnd()};
    const tryNextCandidate=()=>{
      if(!settle())return;
      if(index+1<candidates.length)playVideoCandidates(candidates,index+1,onEnd);
      else if(onEnd)onEnd();
    };
    state.videoIndex=index;
    const v=candidates[index];
    setNowThumbnail(v);
    state.mediaMode='youtube';
    ensureYt(()=>{
      if(!isCurrentAttempt())return;
      const events={
        onReady:e=>{
          if(!isCurrentAttempt())return;
          try{e.target.playVideo();e.target.setPlaybackRate(state.speed)}catch(_){}
        },
        onAutoplayBlocked:()=>{
          if(!isCurrentAttempt())return;
          pill('브라우저가 자동재생을 차단했습니다. 일시정지 버튼을 눌러 재생을 시작하세요.',false);
          showError('자동재생이 차단되었습니다. 화면의 일시정지/재생 버튼을 한 번 눌러주세요.');
        },
        onStateChange:e=>{
          if(!isCurrentAttempt())return;
          if(e.data===1){
            state.isPaused=false;
            state.startedId=state.current&&state.current.id;
            api({view:'player_state',state:'playing',id:String(state.current&&state.current.id||'')});
          }
          if(e.data===2){
            state.isPaused=true;
            api({view:'player_state',state:'paused',id:String(state.current&&state.current.id||'')});
          }
          // 정상 종료는 현재 예약곡의 종료다. 검색 결과의 다른 후보는 오류 시에만 사용한다.
          if(e.data===0)finish();
        },
        onError:e=>{
          if(!isCurrentAttempt())return;
          const code=e&&e.data;
          if(code===101||code===150||code===153){
            pill('이 영상은 다른 사이트에서 재생할 수 없어 다음 후보로 넘어갑니다.',false);
            reportMrError(state.current,v.id,'embed_disabled');
          }
          tryNextCandidate();
        },
      };
      // 각 후보마다 별도 플레이어를 만들고, 이전 후보의 늦은 이벤트는 attemptToken으로 무시한다.
      try{if(state.player&&state.player.destroy)state.player.destroy()}catch(_){}
      state.player=makePlayer($('#ks-yt-player'),v.id,events);
      state.mediaElement=state.player;
    });
  }
  function playLocal(result,onEnd){if(!result.media_url){showError('로컬 MR을 찾았지만 브라우저에서 접근할 웹 URL이 설정되지 않았습니다. MR_LOCAL_PUBLIC_MAPS를 설정하세요.');if(onEnd)onEnd();return}const token=state.playToken;const audio=$('#ks-local-audio'),video=$('#ks-local-video');const isVideo=result.media_type==='video';state.mediaMode=isVideo?'local-video':'local-audio';state.mediaElement=isVideo?video:audio;const el=state.mediaElement;audio.hidden=!(!isVideo);video.hidden=!isVideo;el.src=result.media_url;el.currentTime=0;el.playbackRate=state.speed;el.onended=()=>{if(token===state.playToken&&onEnd)onEnd()};el.onerror=()=>{if(token!==state.playToken)return;showError('로컬 MR 재생에 실패했습니다.');onEnd&&onEnd()};el.onplay=()=>{if(token===state.playToken){state.isPaused=false;api({view:'player_state',state:'playing',id:String(state.current&&state.current.id||'')})}};el.onpause=()=>{if(token===state.playToken&&!el.ended){state.isPaused=true;api({view:'player_state',state:'paused',id:String(state.current&&state.current.id||'')})}};el.play().catch(()=>{if(token!==state.playToken)return;pill('브라우저가 자동재생을 차단했습니다. 재생 버튼을 눌러주세요.',false);showError('로컬 MR 자동재생이 차단되었습니다. 플레이어의 재생 버튼을 눌러주세요.')})}
  function playResolved(result,onEnd){if(!result)return;if(result.source==='local'){playLocal(result,onEnd);return}const candidates=result.candidates&&result.candidates.length?result.candidates:[{id:result.video_id,title:result.title,channel:result.channel,thumbnail:result.thumbnail,embed_url:result.embed_url,watch_url:result.watch_url}];state.videos=candidates;state.videoIndex=0;playVideoCandidates(candidates,0,onEnd)}
  async function cancelOrphanQueueEntry(){
    // 예약 대기열에서 재생 중이던 곡을 두고 다른 곡을 미리듣기(MR)하면, 백엔드에는 그 곡이
    // 계속 "재생 중"으로 남아 2.5초 폴링이 그 곡을 자동으로 되살리는 버그가 있었다.
    // MR 미리듣기를 시작하기 전에 현재 재생 곡만 정리하고(advance=0), 다음 대기곡까지
    // 연쇄로 자동 시작되지 않게 한다 - 대기열은 그대로 보존된다.
    if(state.current&&state.current.id){
      try{const d=await api({view:'queue_cancel',id:String(state.current.id),advance:'0'});renderQueue(d);if(d.success)await refreshHistoryMini()}catch(_){}
    }
  }
  async function logPreviewHistory(song){if(!song)return null;const d=await api({view:'history_log_preview',song:JSON.stringify(song)});if(d.success)await refreshHistoryMini();return d}
  async function playMr(song){state.playToken++;const token=state.playToken;await cancelOrphanQueueEntry();if(token!==state.playToken)return;state.previewMode=true;nowTitle.textContent=(song.title||'')+' MR';nowMeta.textContent='MR 탐색 준비…';const r=await resolveMr(song);if(token!==state.playToken||!r)return;state.current=null;updateNow({title:song.title,singer:song.singer,requester:''});await logPreviewHistory(song);if(token!==state.playToken)return;playResolved(r,()=>{stopPlayer()})}
  async function playQueued(item){
    if(!item)return;
    const token=++state.playToken;
    state.previewMode=false;state.resolving=true;state.current=item;
    updateNow(item);nowMeta.textContent='MR 탐색 중…';
    let r=await resolveMr(item);
    state.resolving=false;
    if(token!==state.playToken||!state.current||String(state.current.id)!==String(item.id))return;
    if(!r){
      updateResumeAffordance();
      const err=await api({view:'queue_error',id:String(item.id),error:'MR 탐색 실패'});
      if(token!==state.playToken)return;
      renderQueue(err);
      await refreshHistoryMini();
      if(token!==state.playToken)return;
      const d=await api({view:'queue_next'});
      if(token!==state.playToken)return;
      renderQueue(d);
      if(d.current)playQueued(d.current);
      return
    }
    playResolved(r,async()=>{
      if(token!==state.playToken)return;
      const d=await api({view:'queue_finish',id:String(item.id)});
      if(token!==state.playToken)return;
      renderQueue(d);
      await refreshHistoryMini();
      if(token!==state.playToken)return;
      if(d.current)await playQueued(d.current);
      else stopPlayer()
    })
  }

  function updateNow(item){nowTitle.textContent=item?item.title||'재생 중인 곡 없음':'재생 중인 곡 없음';nowMeta.textContent=item?((item.singer||'')+(item.requester?' · '+item.requester:'')):'예약곡을 추가하면 자동으로 재생됩니다.';$('#ks-tv-song').textContent=item?item.title||'':'재생 중인 곡 없음';$('#ks-tv-requester').textContent=item&&item.requester?'예약자: '+item.requester:'';updateResumeAffordance()}
  // 다른 BookOasis 영역으로 나갔다가(deactivatePluginView가 정지시킴) 노래방으로
  // 다시 들어오면, 백엔드엔 여전히 "재생 중"으로 남아있는 곡이 있는데(state.current는
  // loadQueue()가 채워줌) 실제 플레이어는 안전을 위해 자동으로 안 붙인다. 이 경우에만
  const resumeBtn=$('#ks-player-resume'),startBtn=$('#ks-player-start'),pauseBtn=$('#ks-player-pause'),stopBtn=$('#ks-player-stop'),nextBtn=$('#ks-player-next'),nowActionsEl=$('.ks-now-actions');
  const tvStartBtn=$('#ks-tv-start'),tvPauseBtn=$('#ks-tv-pause'),tvStopBtn=$('#ks-tv-stop'),tvNextBtn=$('#ks-tv-next');
  function updateResumeAffordance(){
    const hasQueueCurrent=!!state.current;
    const hasPlayback=hasQueueCurrent||state.previewMode;
    const hasPlayer=!!(state.player||state.mediaElement);
    const canResume=!!(hasQueueCurrent&&!state.resolving&&!hasPlayer);
    const canStart=!!(!hasQueueCurrent&&!state.previewMode&&!state.stoppingCurrent&&state.waitingCount>0);
    startBtn.hidden=!canStart;
    pauseBtn.hidden=!hasPlayback||state.resolving||!hasPlayer;
    resumeBtn.hidden=!canResume;
    stopBtn.hidden=!hasPlayback;
    nextBtn.hidden=!hasQueueCurrent;
    const visible=[startBtn,pauseBtn,resumeBtn,stopBtn,nextBtn].filter(x=>x&&!x.hidden).length;
    nowActionsEl.classList.toggle('ks-now-actions--3col',visible===3);
    nowActionsEl.classList.toggle('ks-now-actions--single',visible===1);
    nowActionsEl.classList.toggle('ks-now-actions--empty',visible===0);
    tvStartBtn.hidden=!canStart;
    tvPauseBtn.hidden=!hasPlayback||state.resolving||!hasPlayer;
    tvStopBtn.hidden=!hasPlayback;
    tvNextBtn.hidden=!hasQueueCurrent;
  }
  resumeBtn.onclick=()=>{if(state.current)playQueued(state.current)};
  startBtn.onclick=nextQueue;
  tvStartBtn.onclick=nextQueue;
  stopBtn.onclick=stopCurrent;
  tvStopBtn.onclick=stopCurrent;
  async function stopCurrent(){
    const itemId=state.current&&state.current.id;
    if(itemId)state.stoppingCurrent=true;
    stopPlayer();
    if(!itemId)return;
    try{
      const d=await api({view:'queue_cancel',id:String(itemId),advance:'0'});
      if(d.success){
        renderQueue(d);
        await refreshHistoryMini();
        pill('현재 곡을 중단했습니다. 다음 예약곡은 대기 상태입니다.',true)
      }else{
        const latest=await api({view:'queue'});
        if(latest.success)renderQueue(latest);
        showError(d.error||'현재 곡을 중단하지 못했습니다.');
        pill('중단에 실패했습니다.',false)
      }
    }finally{
      state.stoppingCurrent=false;
      updateResumeAffordance()
    }
  }
  function stopPlayer(){state.playToken++;state.previewMode=false;state.isPaused=false;state.resolving=false;state.current=null;state.startedId=null;state.videos=[];state.mrCandidates=[];state.mrResult=null;progressEl.style.width='0%';nowTime.textContent='00:00 / 00:00';updateNow(null);setNowThumbnail(null);try{if(state.player&&state.player.stopVideo)state.player.stopVideo()}catch(_){}try{if(state.player&&state.player.destroy)state.player.destroy()}catch(_){}state.player=null;try{['#ks-local-audio','#ks-local-video'].forEach(sel=>{const el=$(sel);if(el){el.pause();el.removeAttribute('src');el.load()}})}catch(_){}state.mediaElement=null;state.mediaMode='youtube';pill('재생 대기',true)}
  function seekBy(delta){try{if(state.mediaMode==='youtube'&&state.player&&state.player.getCurrentTime&&state.player.seekTo){const cur=state.player.getCurrentTime()||0;const dur=state.player.getDuration()||0;let target=cur+delta;if(target<0)target=0;if(dur&&target>dur)target=dur;state.player.seekTo(target,true)}else if(state.mediaElement&&state.mediaElement.currentTime!==undefined){const el=state.mediaElement;let target=(el.currentTime||0)+delta;if(target<0)target=0;if(el.duration&&target>el.duration)target=el.duration;el.currentTime=target}}catch(_){}}
  function seekToFraction(frac){frac=Math.max(0,Math.min(1,frac));try{if(state.mediaMode==='youtube'&&state.player&&state.player.getDuration&&state.player.seekTo){const dur=state.player.getDuration()||0;if(dur)state.player.seekTo(dur*frac,true)}else if(state.mediaElement&&state.mediaElement.duration){state.mediaElement.currentTime=state.mediaElement.duration*frac}}catch(_){}}
  function stopPreviewIfActive(){
    // 노래방 플러그인 "안"에서의 이동(카테고리/자모/페이지/검색/인기차트·즐겨찾기 등 영역
    // 전환)은 실제 재생 중인 예약곡을 끊으면 안 된다는 피드백으로 되돌림. 미리듣기(MR
    // 버튼) 중일 때만 멈추고, 실제 예약곡 재생은 그대로 둔다. 플러그인을 완전히 벗어날
    // 때(다른 BookOasis 영역으로 이동)만 멈추는 건 아래 deactivatePluginView()에서
    // 별도로 처리한다.
    if(state.previewMode)stopPlayer();
}
  function togglePause(){try{if(state.mediaMode==='youtube'&&state.player){if(state.isPaused)state.player.playVideo();else state.player.pauseVideo();return}if(state.mediaElement){if(state.mediaElement.paused)state.mediaElement.play();else state.mediaElement.pause()}}catch(_){} }
  async function pinManualMr(song){
    const url=prompt('"'+(song.title||'')+'" 곡에 항상 사용할 유튜브 URL(또는 video ID)을 입력하세요.\n비워두고 확인을 누르면 지정을 해제합니다.','');
    if(url===null)return; // 취소
    if(url.trim()===''){
      const d=await api({view:'mr_clear_manual',song:JSON.stringify(song)});
      pill(d.success?'MR 직접 지정 해제됨':(d.error||'해제 실패'),d.success);
      return;
    }
    const d=await api({view:'mr_set_manual',song:JSON.stringify(song),url:url.trim()});
    pill(d.success?'MR URL 지정 완료':(d.error||'지정 실패'),d.success);
  }
  async function addCustomSong(){
    const title=prompt('추가할 곡 제목을 입력하세요.','');
    if(!title||!title.trim())return;
    const singer=prompt('가수(선택, 비워둬도 됩니다)','')||'';
    const url=prompt('이 곡의 유튜브 MR URL(선택, 비워두면 나중에 MR 버튼에서 직접 지정 가능)','')||'';
    const d=await api({view:'add_custom_song',title:title.trim(),singer:singer.trim(),url:url.trim()});
    if(d.success){pill('곡 추가 완료: '+title.trim(),true);stopPreviewIfActive();state.page=1;refresh()}
    else pill(d.error||'곡 추가 실패',false);
  }
  function historyStatusLabel(status){
    return ({preview:'미리듣기',played:'예약 재생',skipped:'건너뜀',cancelled:'중단',error:'재생 오류'})[status]||'재생 기록'
  }
  function renderHistoryMiniRows(history){
    $('#ks-history-mini').innerHTML=(history||[]).slice(0,4).map(x=>{
      const label=historyStatusLabel(x.status);
      const badge=label==='예약 재생'?'':' <span class="ks-preview-tag">'+esc(label)+'</span>';
      return '<div><b>'+esc(x.title)+badge+'</b><small>'+esc(x.singer||'')+'</small></div>'
    }).join('')||'<span class="ks-muted">아직 재생 기록이 없습니다.</span>'
  }
  function renderHistoryRows(history){
    const songs=(history||[]).map(x=>({
      brand:x.brand,no:x.no,title:x.title,singer:x.singer,
      brand_label:x.brand==='tj'?'TJ':x.brand==='kumyoung'?'금영':(x.brand||''),
      _meta:historyStatusLabel(x.status)+(x.requester?' · '+x.requester:'')+' · '+new Date(x.finished_at*1000).toLocaleString(),
    }));
    state.songs=songs;state.total=songs.length;
    renderSongs()
  }
  async function refreshHistoryMini(){
    const revision=++state.historyRevision;
    const d=await api({view:'history',limit:'50'});
    if(revision!==state.historyRevision||!d.success)return null;
    renderHistoryMiniRows(d.history||[]);
    if(state.mode==='history')renderHistoryRows(d.history||[]);
    return d
  }
  async function clearHistory(){
    if(!confirm('재생 기록을 모두 삭제할까요? 현재 재생과 예약 대기열에는 영향이 없습니다. 삭제한 기록은 복구할 수 없습니다.'))return;
    const d=await api({view:'history_clear'});
    if(!d.success){showError(d.error||'재생 기록을 삭제하지 못했습니다.');return}
    state.historyRevision++;
    renderHistoryMiniRows([]);
    if(state.mode==='history')renderHistoryRows([]);
    pill('재생 기록 '+Number(d.removed||0)+'건을 삭제했습니다.',true)
  }
  async function openTv(){const overlay=$('#ks-tv-overlay');overlay.hidden=false;state.tvOpen=true;document.body.classList.add('ks-tv-active');
    // 주의: overlay 자신이 아니라 문서 루트(documentElement)를 전체화면으로 켠다.
    // 실제 영상이 들어있는 .ks-now-media는 overlay의 자식이 아니라 형제 요소라서,
    // overlay만 fullscreen 시키면 브라우저가 overlay의 하위 요소만 그려서 영상이
    // 화면에 전혀 나오지 않고(소리만 나옴) 검은 화면만 보이는 버그가 있었다.
    const root=document.documentElement;
    try{if(root.requestFullscreen&&!document.fullscreenElement)await root.requestFullscreen()}catch(_){} }
  async function closeTv(){try{if(document.fullscreenElement&&document.exitFullscreen)await document.exitFullscreen()}catch(_){}state.tvOpen=false;document.body.classList.remove('ks-tv-active');$('#ks-tv-overlay').hidden=true}
  async function finishCurrent(){if(!state.current)return;const currentId=String(state.current.id),token=state.playToken;const d=await api({view:'queue_finish',id:currentId});if(token!==state.playToken)return;renderQueue(d);await refreshHistoryMini();if(token!==state.playToken)return;if(d.current)await playQueued(d.current);else stopPlayer()}
  async function playNow(id){if(state.current&&String(state.current.id)===String(id))return;const token=state.playToken;const d=await api({view:'queue_play_now',id:String(id)});if(token!==state.playToken)return;if(!d.success){showError(d.error||'즉시 재생에 실패했습니다.');return}renderQueue(d);await refreshHistoryMini();if(token!==state.playToken)return;if(d.current)await playQueued(d.current)}
  function updateNavActive(){
    container.querySelectorAll('.ks-nav-item').forEach(b=>{
      const nav=b.dataset.nav;
      let active=false;
      if(state.mode==='popular'&&nav==='popular')active=true;
      else if(state.mode==='history'&&nav==='history')active=true;
      else if(state.mode==='tjchart'&&nav==='tjchart')active=true;
      else if(state.mode==='kychart'&&nav==='kychart')active=true;
      else if(state.mode==='list'&&state.favoritesOnly&&nav==='favorites')active=true;
      else if(state.mode==='list'&&!state.favoritesOnly&&nav==='home')active=true;
      b.classList.toggle('is-active',active);
    });
  }
  function setListHead(iconClass,title,sub,showBack){
    $('#ks-list-title').innerHTML='<i class="'+iconClass+'"></i> '+esc(title);
    $('#ks-list-sub').textContent=sub||'';
    $('#ks-back-catalog').hidden=!showBack;
    $('#ks-history-clear').hidden=state.mode!=='history';
    $('#ks-tjchart-top').hidden=true;$('#ks-tjchart-hot').hidden=true;
    $('#ks-kychart-d').hidden=true;$('#ks-kychart-w').hidden=true;$('#ks-kychart-m').hidden=true;$('#ks-kychart-y').hidden=true;
    updateNavActive();
  }
  function backToCatalog(){state.mode='list';state.favoritesOnly=false;state.page=1;refresh();window.scrollTo({top:0,behavior:'smooth'})}
  async function setFavoritesOnly(on){state.mode='list';state.favoritesOnly=on;state.page=1;await refresh()}
  async function showPopularInline(){
    stopPreviewIfActive();
    state.mode='popular';
    paginationEl.innerHTML='';
    setListHead('fa-solid fa-fire','인기차트','재생 기록을 집계해 많이 재생된 순으로 보여줍니다.',true);
    const d=await api({view:'popular',limit:'30'});
    const songs=(d.songs||[]).map((x,i)=>Object.assign({},x,{_meta:(i+1)+'위 · '+Number(x.play_count||0).toLocaleString()+'회 재생'}));
    state.songs=songs;state.total=songs.length;
    renderSongs();
  }
  async function showHistoryInline(){
    stopPreviewIfActive();
    state.mode='history';
    paginationEl.innerHTML='';
    setListHead('fa-solid fa-clock-rotate-left','재생 기록','최근 재생한 곡입니다. 각 기록을 눌러 다시 예약할 수 있습니다.',true);
    const revision=++state.historyRevision;
    const d=await api({view:'history',limit:'50'});
    if(revision!==state.historyRevision||state.mode!=='history')return;
    if(!d.success){showError(d.error||'재생 기록을 불러오지 못했습니다.');return}
    renderHistoryMiniRows(d.history||[]);
    renderHistoryRows(d.history||[])
  }
  async function showTjChartInline(chartType){
    stopPreviewIfActive();
    state.mode='tjchart';
    state.tjChartType=chartType||'TOP';
    paginationEl.innerHTML='';
    setListHead('fa-solid fa-ranking-star',state.tjChartType==='HOT'?'TJ HOT100':'TJ TOP100','TJ미디어 공식 차트입니다(tjmedia.com). 카탈로그에 없는 곡도 MR은 재생해볼 수 있습니다.',true);
    $('#ks-tjchart-top').hidden=false;$('#ks-tjchart-hot').hidden=false;
    $('#ks-tjchart-top').classList.toggle('is-active',state.tjChartType==='TOP');
    $('#ks-tjchart-hot').classList.toggle('is-active',state.tjChartType==='HOT');
    listEl.innerHTML='<div class="ks-muted" style="padding:20px">불러오는 중…</div>';
    const d=await api({view:'tj_chart',chart_type:state.tjChartType});
    if(state.mode!=='tjchart')return; // 로딩 중 다른 화면으로 이동했으면 무시
    if(!d.success){listEl.innerHTML='';emptyEl.hidden=false;showError(d.error||'TJ 차트를 가져오지 못했습니다.');state.songs=[];state.total=0;return}
    showError('');
    state.songs=d.songs||[];state.total=state.songs.length;
    renderSongs();
  }
  const KY_PERIOD_LABEL={'':'일간','w':'주간','m':'월간','y':'연간'};
  async function showKyChartInline(period){
    stopPreviewIfActive();
    state.mode='kychart';
    state.kyChartPeriod=(period===undefined||period===null)?'':period;
    paginationEl.innerHTML='';
    setListHead('fa-solid fa-ranking-star','금영 '+KY_PERIOD_LABEL[state.kyChartPeriod]+' 인기차트','금영(KYSing) 공식 차트입니다(kysing.kr). 카탈로그에 없는 곡도 MR은 재생해볼 수 있습니다.',true);
    const btnMap={'':'#ks-kychart-d','w':'#ks-kychart-w','m':'#ks-kychart-m','y':'#ks-kychart-y'};
    Object.values(btnMap).forEach(sel=>{$(sel).hidden=false;$(sel).classList.remove('is-active')});
    $(btnMap[state.kyChartPeriod]).classList.add('is-active');
    listEl.innerHTML='<div class="ks-muted" style="padding:20px">불러오는 중…</div>';
    const d=await api({view:'ky_chart',period:state.kyChartPeriod});
    if(state.mode!=='kychart')return;
    if(!d.success){listEl.innerHTML='';emptyEl.hidden=false;showError(d.error||'금영 차트를 가져오지 못했습니다.');state.songs=[];state.total=0;return}
    showError('');
    state.songs=d.songs||[];state.total=state.songs.length;
    renderSongs();
  }
  function navFocus(which){
    if(which==='queue')document.querySelector('.ks-queue-card')?.scrollIntoView({behavior:'smooth',block:'start'});
    else if(which==='current')document.querySelector('.ks-now-card')?.scrollIntoView({behavior:'smooth',block:'start'});
    else if(which==='history')showHistoryInline();
    else if(which==='popular')showPopularInline();
    else if(which==='tjchart')showTjChartInline(state.tjChartType||'TOP');
    else if(which==='kychart')showKyChartInline(state.kyChartPeriod||'');
    else if(which==='favorites')setFavoritesOnly(!state.favoritesOnly);
    else if(which==='home')backToCatalog();
    else if(which==='search'){if(state.mode!=='list')backToCatalog();searchEl.focus();window.scrollTo({top:0,behavior:'smooth'})}
  }
  // 이벤트
  container.querySelectorAll('.ks-cat').forEach(b=>b.onclick=()=>{stopPreviewIfActive();container.querySelectorAll('.ks-cat').forEach(x=>x.classList.remove('is-active'));b.classList.add('is-active');state.brand=b.dataset.brand;state.page=1;refresh()});
  container.querySelectorAll('.ks-letter').forEach(b=>b.onclick=()=>{stopPreviewIfActive();container.querySelectorAll('.ks-letter').forEach(x=>x.classList.remove('is-active'));b.classList.add('is-active');state.category=b.dataset.category;state.letter='all';state.page=1;refresh()});
  container.querySelectorAll('.ks-nav-item').forEach(b=>b.onclick=()=>{stopPreviewIfActive();navFocus(b.dataset.nav)});
  $('#ks-save-requester').onclick=()=>{const v=(requesterEl.value||'').trim();if(v)localStorage.setItem('ks-requester',v);pill(v?'예약자 '+v+' 저장':'예약자 이름을 입력하세요',!!v)};
  requesterEl.value=localStorage.getItem('ks-requester')||'';
  requesterEl.addEventListener('input',()=>{const v=requesterEl.value.trim();const mineEl=$('#ks-my-queue');if(mineEl)mineEl.textContent=v?'예약 확인 중…':'예약자 이름을 입력하세요'});
  let timer;searchEl.oninput=()=>{stopPreviewIfActive();clearTimeout(timer);timer=setTimeout(()=>{state.page=1;refresh()},250)};searchEl.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();stopPreviewIfActive();state.page=1;refresh()}};
  // 이벤트 위임 방식으로 바인딩해 검색 버튼이 어떤 이유로든(재렌더링 등) 누락되지 않도록 한다.
  container.addEventListener('click',e=>{const btn=e.target.closest('#ks-search-btn');if(btn){e.preventDefault();stopPreviewIfActive();clearTimeout(timer);state.page=1;refresh()}});
  $('#ks-next').onclick=finishCurrent;$('#ks-player-next').onclick=finishCurrent;$('#ks-tv-next').onclick=finishCurrent;$('#ks-clear').onclick=clearQueue;$('#ks-player-pause').onclick=togglePause;$('#ks-tv-pause').onclick=togglePause;$('#ks-history-clear').onclick=clearHistory;$('#ks-player-mode').onclick=openTv;$('#ks-now-tv').onclick=openTv;$('#ks-tv-close').onclick=closeTv;$('#ks-history-more').onclick=showHistoryInline;
  $('#ks-back-catalog').onclick=()=>{stopPreviewIfActive();backToCatalog()};
  $('#ks-tjchart-top').onclick=()=>showTjChartInline('TOP');
  $('#ks-tjchart-hot').onclick=()=>showTjChartInline('HOT');
  $('#ks-kychart-d').onclick=()=>showKyChartInline('');
  $('#ks-kychart-w').onclick=()=>showKyChartInline('w');
  $('#ks-kychart-m').onclick=()=>showKyChartInline('m');
  $('#ks-kychart-y').onclick=()=>showKyChartInline('y');
  function applySpeed(){try{if(state.mediaMode==='youtube'&&state.player&&state.player.setPlaybackRate)state.player.setPlaybackRate(state.speed);else if(state.mediaElement)state.mediaElement.playbackRate=state.speed}catch(_){}}
  container.querySelectorAll('.ks-speed-btn').forEach(b=>b.onclick=()=>{container.querySelectorAll('.ks-speed-btn').forEach(x=>x.classList.remove('is-active'));b.classList.add('is-active');state.speed=Number(b.dataset.speed)||1;applySpeed()});
  $('#ks-seek-back').onclick=()=>seekBy(-10);$('#ks-seek-fwd').onclick=()=>seekBy(10);
  $('#ks-now-progress-wrap').onclick=e=>{const rect=e.currentTarget.getBoundingClientRect();seekToFraction((e.clientX-rect.left)/rect.width)};
  // --- 마이크 이펙트(에코) -----------------------------------------------------
  // 스피커 환경에서 마이크 에코를 쓰면 하울링(음향 피드백) 위험이 있어 3중 안전장치를 둔다:
  // 1) 브라우저 내장 에코 캔슬레이션(echoCancellation) - 1차 방어선
  // 2) 에코 피드백량을 슬라이더 자체에서 안전한 범위(0~0.35)로 상한 캡 - 이 값 이상은
  //    보통 어쿠스틱 결합 없이도 이펙트 자체가 자체발진하기 시작하는 임계값이라 원천 차단
  // 3) 실시간 하울링 감지(좁은 대역 급상승 패턴) + 그 주파수 노치 필터 + 순간 덕킹
  const mic = {ctx:null,stream:null,source:null,delay:null,feedback:null,wet:null,dry:null,notch:null,compressor:null,analyser:null,outGain:null,enabled:false,watching:false,howlData:null,lastHowlAt:0,recorder:null};
  function micParams(){return {echo:Number($('#ks-mic-echo').value)/100,delay:Number($('#ks-mic-delay').value)/1000,volume:Number($('#ks-mic-volume').value)/100}}
  function applyMicParams(){if(!mic.enabled)return;const p=micParams();mic.feedback.gain.setTargetAtTime(Math.min(0.35,p.echo),mic.ctx.currentTime,0.05);mic.wet.gain.setTargetAtTime(Math.min(0.5,p.echo+0.15),mic.ctx.currentTime,0.05);mic.delay.delayTime.setTargetAtTime(p.delay,mic.ctx.currentTime,0.05);mic.baseOutVolume=Math.min(1,p.volume);if(!mic.ducked)mic.outGain.gain.setTargetAtTime(mic.baseOutVolume,mic.ctx.currentTime,0.05)}
  function flashHowlDot(){const dot=$('#ks-mic-howl-dot');if(!dot)return;dot.hidden=false;clearTimeout(mic._dotTimer);mic._dotTimer=setTimeout(()=>{dot.hidden=true},1200)}
  function triggerHowlSuppression(freq){
    const now=mic.ctx.currentTime;
    mic.notch.frequency.setTargetAtTime(freq,now,0.01);
    mic.notch.Q.value=14;
    // 발진 신호를 순간적으로 확 낮췄다가(덕킹) 0.6초 뒤부터 서서히 원래 볼륨으로 복구한다.
    mic.ducked=true;
    mic.outGain.gain.cancelScheduledValues(now);
    mic.outGain.gain.setTargetAtTime(0.1,now,0.01);
    mic.outGain.gain.setTargetAtTime(mic.baseOutVolume||0.8,now+0.6,0.35);
    setTimeout(()=>{mic.ducked=false},1000);
    flashHowlDot();
  }
  function watchHowl(){
    if(!mic.enabled||!mic.analyser)return;
    mic.analyser.getByteFrequencyData(mic.howlData);
    let maxVal=0,maxIdx=0,sum=0;
    for(let i=4;i<mic.howlData.length;i++){sum+=mic.howlData[i];if(mic.howlData[i]>maxVal){maxVal=mic.howlData[i];maxIdx=i}}
    const avg=sum/(mic.howlData.length-4);
    const now=performance.now();
    // 좁은 대역이 평균 대비 비정상적으로(6배 이상) 튀고, 절대값도 충분히 크면 하울링 시그니처로 판단.
    // 같은 발진이 계속 잡히지 않도록 최소 200ms 간격을 둔다.
    if(maxVal>220&&maxVal>avg*6&&(now-mic.lastHowlAt)>200){
      mic.lastHowlAt=now;
      const freq=maxIdx*mic.ctx.sampleRate/mic.analyser.fftSize;
      triggerHowlSuppression(freq);
    }
    requestAnimationFrame(watchHowl);
  }
  async function enableMicEffect(){
    if(mic.enabled)return;
    try{
      const stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
      mic.stream=stream;
      mic.ctx=mic.ctx||new (window.AudioContext||window.webkitAudioContext)();
      if(mic.ctx.state==='suspended')await mic.ctx.resume();
      const ctx=mic.ctx;
      mic.source=ctx.createMediaStreamSource(stream);
      mic.dry=ctx.createGain();mic.dry.gain.value=1;
      mic.delay=ctx.createDelay(1.0);mic.delay.delayTime.value=micParams().delay;
      mic.feedback=ctx.createGain();mic.feedback.gain.value=Math.min(0.35,micParams().echo);
      mic.wet=ctx.createGain();mic.wet.gain.value=Math.min(0.5,micParams().echo+0.15);
      mic.notch=ctx.createBiquadFilter();mic.notch.type='notch';mic.notch.frequency.value=2000;mic.notch.Q.value=1;
      mic.compressor=ctx.createDynamicsCompressor();mic.compressor.threshold.value=-18;mic.compressor.ratio.value=8;
      mic.analyser=ctx.createAnalyser();mic.analyser.fftSize=2048;mic.howlData=new Uint8Array(mic.analyser.frequencyBinCount);
      mic.outGain=ctx.createGain();mic.baseOutVolume=micParams().volume;mic.outGain.gain.value=mic.baseOutVolume;
      // 원음(dry) + 지연 피드백 에코(wet)를 합쳐서 압축 -> 노치(하울링 시에만 개입) -> 출력
      mic.source.connect(mic.dry);
      mic.source.connect(mic.delay);
      mic.delay.connect(mic.feedback);
      mic.feedback.connect(mic.delay);
      mic.delay.connect(mic.wet);
      mic.dry.connect(mic.compressor);
      mic.wet.connect(mic.compressor);
      mic.compressor.connect(mic.notch);
      mic.notch.connect(mic.analyser);
      mic.analyser.connect(mic.outGain);
      mic.outGain.connect(ctx.destination);
      mic.enabled=true;mic.ducked=false;mic.lastHowlAt=0;
      requestAnimationFrame(watchHowl);
      const btn=$('#ks-mic-toggle');btn.classList.add('is-on');btn.innerHTML='<i class="fa-solid fa-microphone-slash"></i> 마이크 끄기';
      $('#ks-mic-sliders').hidden=false;
      $('#ks-mic-record-row').hidden=false;
      pill('마이크 에코 켜짐',true);
    }catch(err){
      showError('마이크 권한을 허용해야 에코 효과를 쓸 수 있습니다.');
    }
  }
  function stopMicRecording(){
    if(mic.recorder&&mic.recorder.state!=='inactive'){try{mic.recorder.stop()}catch(_){}}
    mic.recorder=null;
    const btn=$('#ks-mic-record');if(btn){btn.classList.remove('is-recording');btn.innerHTML='<i class="fa-solid fa-circle"></i> 내 목소리 녹음'}
  }
  function disableMicEffect(){
    if(!mic.enabled)return;
    stopMicRecording();
    try{if(mic.stream)mic.stream.getTracks().forEach(t=>t.stop())}catch(_){}
    try{mic.source&&mic.source.disconnect()}catch(_){}
    try{mic.outGain&&mic.outGain.disconnect()}catch(_){}
    mic.enabled=false;mic.stream=null;
    const btn=$('#ks-mic-toggle');btn.classList.remove('is-on');btn.innerHTML='<i class="fa-solid fa-microphone"></i> 마이크 켜기';
    $('#ks-mic-sliders').hidden=true;$('#ks-mic-howl-dot').hidden=true;
    $('#ks-mic-record-row').hidden=true;$('#ks-mic-record-result').hidden=true;
    pill('마이크 에코 꺼짐',true);
  }
  $('#ks-mic-toggle').onclick=()=>{if(mic.enabled)disableMicEffect();else enableMicEffect()};
  $('#ks-mic-record').onclick=()=>{
    if(!mic.enabled||!mic.stream)return;
    if(mic.recorder&&mic.recorder.state==='recording'){stopMicRecording();return}
    try{
      const chunks=[];
      // 원음(마이크 입력) 그대로 녹음한다 - 에코/노치 등 이펙트 체인과는 별개의 순수 녹음.
      const recorder=new MediaRecorder(mic.stream);
      recorder.ondataavailable=e=>{if(e.data&&e.data.size>0)chunks.push(e.data)};
      recorder.onstop=()=>{
        const blob=new Blob(chunks,{type:recorder.mimeType||'audio/webm'});
        const url=URL.createObjectURL(blob);
        const box=$('#ks-mic-record-result');
        box.innerHTML='<audio controls src="'+url+'"></audio><a href="'+url+'" download="my-recording.webm"><i class="fa-solid fa-download"></i> 다운로드</a>';
        box.hidden=false;
      };
      recorder.start();
      mic.recorder=recorder;
      const btn=$('#ks-mic-record');btn.classList.add('is-recording');btn.innerHTML='<i class="fa-solid fa-circle"></i> 녹음 중… (눌러서 종료)';
    }catch(_){
      showError('이 브라우저에서는 녹음 기능을 지원하지 않습니다.');
    }
  };
  ['ks-mic-echo','ks-mic-delay','ks-mic-volume'].forEach(id=>{
    const el=$('#'+id);
    el.oninput=()=>{
      $('#'+id+'-val').textContent=id==='ks-mic-delay'?el.value+'ms':el.value+'%';
      applyMicParams();
    };
  });
  $('#ks-sync-tj').onclick=async()=>{const full=$('#ks-sync-full').checked;const d=await api({view:'sync_start',brand:'tj',full:full?'1':'0'});pill(d.success?(full?'TJ 전체 동기화 시작':'TJ 동기화 시작'):(d.error||'동기화 실패'),d.success);pollSync()};$('#ks-sync-ky').onclick=async()=>{const full=$('#ks-sync-full').checked;const d=await api({view:'sync_start',brand:'kumyoung',full:full?'1':'0'});pill(d.success?(full?'금영 전체 동기화 시작':'금영 동기화 시작'):(d.error||'동기화 실패'),d.success);pollSync()};$('#ks-sync-stop').onclick=async()=>{const d=await api({view:'sync_stop'});pill('동기화 중지 요청',d.success)};
  $('#ks-export-seed').onclick=async()=>{pill('시드 내보내는 중…',true);const d=await api({view:'export_seed'});pill(d.success?('시드 저장 완료 · '+Number(d.count||0).toLocaleString()+'곡'):(d.error||'시드 내보내기 실패'),d.success)};
  $('#ks-add-song').onclick=addCustomSong;
  async function pollSync(){const d=await api({view:'sync_status'});if(d.sync&&d.sync.running){pill(d.sync.message||'동기화 중…',true);setTimeout(pollSync,1200)}else refresh()}
  let pluginViewActive=!!(container&&container.isConnected&&container.style.display!=='none');
  let queuePollTimer=null,progressTimer=null,pluginViewObserver=null;
  function isPluginViewActive(){return pluginViewActive&&!!container&&container.isConnected&&container.style.display!=='none'}
  function onLibraryCategorySelected(event){
    const selectedId=String(event&&event.detail&&event.detail.id||'');
    if(selectedId&&selectedId!=='plugin_'+pluginId)deactivatePluginView();
  }
  function deactivatePluginView(){
    if(!pluginViewActive)return;
    pluginViewActive=false;
    window.removeEventListener('library:category-selected',onLibraryCategorySelected);
    if(queuePollTimer!==null)clearInterval(queuePollTimer);
    if(progressTimer!==null)clearInterval(progressTimer);
    if(pluginViewObserver)pluginViewObserver.disconnect();
    // 현재 예약은 백엔드에 남겨 재진입 시 사용자가 직접 이어 재생할 수 있게 한다.
    // 화면을 벗어난 뒤 폴링이 이 항목을 다시 자동 재생하지 않도록 위에서 타이머도 정리한다.
    stopPlayer();
  }
  window.addEventListener('library:category-selected',onLibraryCategorySelected);
  if(container&&typeof MutationObserver!=='undefined'){
    pluginViewObserver=new MutationObserver(()=>{
      if(container&&container.style.display==='none')deactivatePluginView();
    });
    pluginViewObserver.observe(container,{attributes:true,attributeFilter:['style']});
  }
  queuePollTimer=setInterval(async()=>{
    if(!isPluginViewActive()||state.pollBusy||state.stoppingCurrent)return;
    state.pollBusy=true;
    try{
      const d=await api({view:'queue'});
      if(!isPluginViewActive()||state.stoppingCurrent)return;
      if(d.success){
        if(state.previewMode)return;
        const remote=d.current;
        if(remote&&(!state.current||String(remote.id)!==String(state.current.id))){
          renderQueue(d);
          if(remote.status==='playing')playQueued(remote);
        }else renderQueue(d);
      }
    }finally{state.pollBusy=false}
  },2500);
  progressTimer=setInterval(()=>{if(isPluginViewActive())syncProgress()},1000);

  loadQueue();refresh();refreshHistoryMini();
})();
