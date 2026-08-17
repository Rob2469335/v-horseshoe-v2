import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ActionOnboardingCard } from './action-onboarding-card';
import { useUiStore } from '../../state/ui-store';
import React from 'react';

describe('ActionOnboardingCard', () => {
  beforeEach(() => {
    // Reset store before each test
    useUiStore.setState({ dismissedOnboarding: {} });
  });

  it('renders correctly when not dismissed', () => {
    render(
      <ActionOnboardingCard
        id="test-card"
        title="Test Title"
        description="Test Description"
        actionLabel="Do Action"
        onAction={() => {}}
      />
    );
    expect(screen.getByText('Test Title')).toBeDefined();
    expect(screen.getByText('Do Action')).toBeDefined();
  });

  it('calls onAction when action button is clicked', () => {
    const onActionMock = vi.fn();
    render(
      <ActionOnboardingCard
        id="test-card"
        title="Test Title"
        description="Test Description"
        actionLabel="Do Action"
        onAction={onActionMock}
      />
    );
    fireEvent.click(screen.getByText('Do Action'));
    expect(onActionMock).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('Test Title')).not.toBeNull();
  });

  it('disappears when dismiss button (X) is clicked', () => {
    render(
      <ActionOnboardingCard
        id="test-card"
        title="Test Title"
        description="Test Description"
        actionLabel="Do Action"
        onAction={() => {}}
      />
    );
    expect(screen.getByText('Test Title')).toBeDefined();
    
    const dismissBtn = screen.getByRole('button', { name: 'Dismiss' });
    fireEvent.click(dismissBtn);
    
    expect(screen.queryByText('Test Title')).toBeNull();
  });
  
  it('does not render if already dismissed in store', () => {
    useUiStore.setState({ dismissedOnboarding: { 'test-card': true } });
    render(
      <ActionOnboardingCard
        id="test-card"
        title="Test Title"
        description="Test Description"
        actionLabel="Do Action"
        onAction={() => {}}
      />
    );
    expect(screen.queryByText('Test Title')).toBeNull();
  });
});
