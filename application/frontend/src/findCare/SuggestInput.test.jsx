import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { SuggestInput } from './SuggestInput';

const OPTIONS = ['One', 'Two', 'Three'].map((label) => ({ id: label, label }));

function Field() {
  const [text, setText] = useState('');
  return (
    <SuggestInput label="Pick" icon={() => null} text={text} options={OPTIONS} onTextChange={setText} onPick={() => {}} />
  );
}

const selected = () => screen.getAllByRole('option').map((o) => o.getAttribute('aria-selected') === 'true');

describe('SuggestInput', () => {
  it('lets the arrow keys keep the highlight while the list moves under a resting pointer', () => {
    render(<Field />);
    const input = screen.getByRole('combobox');
    fireEvent.focus(input);
    const [one, two] = screen.getAllByRole('option');

    fireEvent.mouseMove(one, { clientX: 10, clientY: 10 });
    expect(selected()).toEqual([true, false, false]);
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    expect(selected()).toEqual([false, false, true]);

    // The list scrolled, so another option is under the pointer, which hasn't moved.
    fireEvent.mouseMove(two, { clientX: 10, clientY: 10 });
    expect(selected()).toEqual([false, false, true]);
    fireEvent.mouseMove(two, { clientX: 10, clientY: 12 });
    expect(selected()).toEqual([false, true, false]);
  });
});
