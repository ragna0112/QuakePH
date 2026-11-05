import { afterEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'
Object.defineProperty(window,'matchMedia',{writable:true,value:()=>({matches:true,addEventListener:()=>{},removeEventListener:()=>{}})})
HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');this.querySelector<HTMLButtonElement>('button')?.focus()}
HTMLDialogElement.prototype.close=function(){this.removeAttribute('open')}
afterEach(()=>{cleanup();vi.useRealTimers();vi.restoreAllMocks();vi.unstubAllGlobals();window.history.replaceState(null,'','/')})
