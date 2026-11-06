import { useId, useLayoutEffect, useRef, useState } from 'react';
import { clsx } from 'clsx';

// A text field with a pick list, used for the service, the location and the plan.
export function SuggestInput({
  label, hideLabel, icon, placeholder, inputMode, text, options, invalid, className, listClassName,
  onTextChange, onPick, onEnter, onBlur,
}) {
  const Icon = icon;
  const id = useId();
  const listId = `${id}-list`;
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const inputRef = useRef(null);
  const listRef = useRef(null);
  const showList = open && options.length > 0;

  // The list stops above the bottom of what's visible, a phone's keyboard included,
  // and scrolls within that.
  useLayoutEffect(() => {
    if (!showList) return;
    const below = (window.visualViewport?.height ?? window.innerHeight) - inputRef.current.getBoundingClientRect().bottom;
    listRef.current.style.maxHeight = `${Math.max(160, Math.min(384, below - 16))}px`;
  }, [showList]);

  const close = () => {
    setOpen(false);
    setActive(-1);
  };
  const pick = (option) => {
    close();
    onPick(option);
  };

  const onKeyDown = (event) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!showList) {
        setOpen(true);
        return;
      }
      const down = event.key === 'ArrowDown';
      setActive((i) => {
        if (i === -1) return down ? 0 : options.length - 1;
        return (i + (down ? 1 : -1) + options.length) % options.length;
      });
    } else if (event.key === 'Enter') {
      event.preventDefault();
      if (showList && options[active]) {
        pick(options[active]);
      } else {
        close();
        onEnter?.();
      }
    } else if (event.key === 'Escape') {
      close();
    }
  };

  return (
    <div className={clsx('relative min-w-0 flex-1', className)}>
      <label
        htmlFor={id}
        className={hideLabel ? 'sr-only' : 'mb-1.5 block text-sm font-medium text-slate-700'}
      >
        {label}
      </label>
      <div className="relative">
        <Icon size={16} aria-hidden="true" className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400" />
        <input
          ref={inputRef}
          id={id}
          role="combobox"
          aria-expanded={showList}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={showList && active >= 0 ? `${listId}-${active}` : undefined}
          aria-invalid={invalid || undefined}
          autoComplete="off"
          inputMode={inputMode}
          placeholder={placeholder}
          value={text}
          title={text || undefined}
          onChange={(event) => {
            onTextChange(event.target.value);
            setOpen(true);
            setActive(-1);
          }}
          onFocus={(event) => {
            // Selected, so typing replaces it, but showing its start, which says the most.
            event.target.select();
            event.target.scrollLeft = 0;
            setOpen(true);
          }}
          onBlur={() => {
            close();
            onBlur?.();
          }}
          onKeyDown={onKeyDown}
          className={clsx(
            'h-11 w-full truncate rounded-xl border bg-white pl-10 pr-3 text-[15px] text-slate-900 outline-none transition-colors placeholder:text-slate-400',
            'focus:border-emerald-700 focus:ring-2 focus:ring-emerald-700/15',
            invalid ? 'border-rose-400' : 'border-slate-200 hover:border-slate-300',
          )}
        />
      </div>
      {showList && (
        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          aria-label={label}
          onMouseDown={(event) => event.preventDefault()}
          className={clsx(
            'absolute top-full z-30 mt-1.5 max-h-72 overflow-y-auto rounded-xl border border-slate-200 bg-white py-1 shadow-xl shadow-slate-900/10',
            listClassName ? `left-0 right-0 ${listClassName}` : 'left-0 right-0',
          )}
        >
          {options.map((option, index) => (
            <li
              key={option.id}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={index === active}
              onClick={() => pick(option)}
              onMouseEnter={() => setActive(index)}
              className={clsx(
                'flex cursor-pointer items-start gap-3 px-3.5 py-2.5 text-[15px] leading-snug text-slate-900',
                index === active && 'bg-slate-100',
              )}
            >
              {option.badge && (
                <span className={clsx(
                  'mt-px min-w-16 shrink-0 rounded-md px-1.5 py-0.5 text-center text-xs font-semibold ring-1 ring-inset',
                  option.badge.className,
                )}
                >
                  {option.badge.text}
                </span>
              )}
              {/* Whole, however long. */}
              <span className="min-w-0 flex-1 break-words">
                <span className={clsx('block', option.detail && 'font-medium')}>{option.label}</span>
                {option.detail && <span className="block text-sm text-slate-600">{option.detail}</span>}
                {option.hint && <span className="mt-0.5 block text-xs text-slate-500">{option.hint}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
