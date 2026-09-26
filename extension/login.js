const DEFAULT_BASE='https://calreminder.onrender.com';
const $=selector=>document.querySelector(selector);
function randomVerifier(){const bytes=crypto.getRandomValues(new Uint8Array(32));return btoa(String.fromCharCode(...bytes)).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');}
async function challenge(verifier){const bytes=new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(verifier)));return btoa(String.fromCharCode(...bytes)).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');}
function show(message){const status=$('#status');status.textContent=message;status.classList.remove('hidden');}
$('#login').addEventListener('click',async()=>{
  const button=$('#login');button.disabled=true;$('#status').classList.add('hidden');
  try{
    const {base=DEFAULT_BASE}=await chrome.storage.local.get('base');
    const verifier=randomVerifier();
    const url=new URL(`${base}/extension/login`);
    url.searchParams.set('redirect_uri',chrome.identity.getRedirectURL('oauth'));
    url.searchParams.set('code_challenge',await challenge(verifier));
    const result=await chrome.identity.launchWebAuthFlow({url:url.href,interactive:true});
    if(!result)throw new Error('Google 로그인을 완료하지 못했습니다. 다시 시도해 주세요.');
    const code=new URL(result).searchParams.get('code');
    if(!code)throw new Error('로그인 응답에 인증 코드가 없습니다. 다시 시도해 주세요.');
    const response=await fetch(`${base}/api/extension/token`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code,verifier})});
    const data=await response.json();
    if(!response.ok)throw new Error(data.error||'로그인에 실패했습니다.');
    const stateResponse=await fetch(`${base}/api/extension/state`,{headers:{Authorization:`Bearer ${data.token}`}});
    const state=await stateResponse.json();
    if(!stateResponse.ok)throw new Error(state.error||'캘린더를 불러오지 못했습니다.');
    await chrome.storage.local.set({token:data.token});
    show('로그인되었습니다. 이 탭을 닫고 확장 프로그램을 다시 열어 주세요.');
    button.textContent='로그인 완료';
    setTimeout(()=>window.close(),1200);
  }catch(error){show(error.message||'로그인 중 오류가 발생했습니다.');button.disabled=false;}
});
